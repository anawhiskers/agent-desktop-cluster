import copy, hashlib, json, os, shutil, sqlite3, stat, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import archive_backup as a

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='fixtures-', dir=Path(__file__).parent)).absolute()
        self.src = self.root/'source'; self.out = self.root/'archives'; self.restore = self.root/'restores'
        for p in (self.src, self.out, self.restore): p.mkdir(mode=0o700)
        self.db = self.src/'closed.sqlite'
        with sqlite3.connect(self.db) as con:
            con.execute('create table checks (id integer primary key, payload blob)')
            con.executemany('insert into checks(payload) values (?)', [(bytes(range(256))*20,)]*50)
        self.db.chmod(0o600); self.original = self.db.read_bytes()
        self.plan = dict(version=1, source=str(self.db), source_identity=a.identity(self.db.stat()), source_parent_identity=a.identity(self.src.stat(), True), sha256=hashlib.sha256(self.original).hexdigest(), archive_dir=str(self.out/'operation'), archive_parent_identity=a.identity(self.out.stat(), True), uid=os.geteuid(), gid=os.getegid(), max_input_bytes=1024*1024, max_archive_bytes=1024*1024, max_seconds=20, reserve_bytes=0)
    def tearDown(self):
        for p in self.root.rglob('*'):
            if p.is_dir(): p.chmod(0o700)
        shutil.rmtree(self.root); self.assertFalse(self.root.exists())
    def run_archive(self, hook=None): return a.archive_backup(self.plan, _checkpoint=hook)
    def restore_mapping(self, mapping, destination, **overrides):
        budgets=dict(max_archive_bytes=1024*1024, max_output_bytes=1024*1024, max_seconds=20, reserve_bytes=0)
        budgets.update(overrides)
        return a.restore_verified(mapping, destination, **budgets)
    def retained(self, result):
        self.assertNotEqual(result['status'], 'retired_durable', result); self.assertTrue(self.db.exists(), result)
    def test_success_restore_private(self):
        r=self.run_archive(); self.assertEqual(r['status'],'retired_durable',r); self.assertFalse(self.db.exists())
        mapping=json.loads((self.out/'operation'/'verified.json').read_text())['mapping']; self.assertEqual(mapping,r['mapping'])
        restored=self.restore/'new.sqlite'; v=self.restore_mapping(mapping,str(restored)); self.assertEqual(v['status'],'restored_verified'); self.assertEqual(restored.read_bytes(),self.original)
        with sqlite3.connect('file:'+str(restored)+'?mode=ro',uri=True) as con: self.assertEqual(con.execute('select count(*) from checks').fetchone(),(50,))
        for p in (self.out/'operation').rglob('*'): self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE((self.out/'operation').stat().st_mode),0o700)
        with self.assertRaises(FileExistsError): self.restore_mapping(mapping,str(restored))
    def test_wrong_hash(self):
        self.plan['sha256']='0'*64; self.retained(self.run_archive()); self.assertFalse((self.out/'operation').exists())
    def test_wrong_identity(self): self.plan['source_identity']['ino']+=1; self.retained(self.run_archive())
    def test_wrong_owner_mode_size(self):
        for field,value in (('uid',os.geteuid()+1),('gid',os.getegid()+1),('mode',0o644),('size',99)):
            p=copy.deepcopy(self.plan); p['source_identity'][field]=value; self.retained(a.archive_backup(p))
    def test_symlink(self):
        real=self.src/'real.sqlite'; self.db.rename(real); self.db.symlink_to(real); self.retained(self.run_archive())
    def test_hardlink(self): os.link(self.db,self.src/'alias.sqlite'); self.retained(self.run_archive())
    def test_preexisting_operation_archive(self):
        op=self.out/'operation'; op.mkdir(mode=0o700); sentinel=op/'backup.sqlite.gz'; sentinel.write_bytes(b'untouched'); sentinel.chmod(0o600)
        self.plan['archive_parent_identity']=a.identity(self.out.stat(),True); self.retained(self.run_archive()); self.assertEqual(sentinel.read_bytes(),b'untouched')
    def test_preexisting_journal(self):
        def hook(stage):
            if stage=='before_journal': (self.out/'operation'/'verified.json').write_text('existing')
        self.retained(self.run_archive(hook)); self.assertEqual((self.out/'operation'/'verified.json').read_text(),'existing')
    def test_changed_original(self):
        def hook(stage):
            if stage=='before_retire':
                with self.db.open('ab') as f: f.write(b'changed')
        self.retained(self.run_archive(hook))
    def test_replaced_original(self):
        def hook(stage):
            if stage=='before_retire':
                self.db.rename(self.src/'kept.sqlite'); self.db.write_bytes(self.original); self.db.chmod(0o600)
        self.retained(self.run_archive(hook))
    def test_parent_mode(self): self.retained(self.run_archive(lambda s: self.src.chmod(0o750) if s=='before_retire' else None))
    def test_parent_contents(self):
        def hook(s):
            if s=='before_retire': (self.src/'new-entry').write_bytes(b'x')
        self.retained(self.run_archive(hook))
    def test_parent_replaced(self):
        def hook(s):
            if s=='before_retire':
                self.src.rename(self.root/'source-kept'); self.src.mkdir(mode=0o700); self.db.write_bytes(self.original); self.db.chmod(0o600)
        self.retained(self.run_archive(hook)); self.assertTrue((self.root/'source-kept'/'closed.sqlite').exists())
    def test_archive_changed(self):
        def hook(s):
            if s=='before_retire':
                with (self.out/'operation'/'backup.sqlite.gz').open('ab') as f: f.write(b'changed')
        self.retained(self.run_archive(hook))
    def test_archive_parent_replaced(self):
        def hook(s):
            if s=='before_retire': self.out.rename(self.root/'archives-kept'); self.out.mkdir(mode=0o700)
        self.retained(self.run_archive(hook))
    def test_operation_mode(self): self.retained(self.run_archive(lambda s: (self.out/'operation').chmod(0o750) if s=='before_retire' else None))
    def test_journal_changed(self):
        def hook(s):
            if s=='before_retire': (self.out/'operation'/'verified.json').write_text('changed')
        self.retained(self.run_archive(hook))
    def test_failed_unlink(self):
        real=os.unlink
        def fail(name,*args,**kwargs):
            if name==self.db.name: raise PermissionError('injected unlink refusal')
            return real(name,*args,**kwargs)
        with patch.object(a.os,'unlink',fail): r=self.run_archive()
        self.retained(r); self.assertEqual(r['status'],'retirement_uncertain'); self.assertTrue(r['journal_durable'])
    def test_unlink_raises_after_removal(self):
        real=os.unlink
        def fail(name,*args,**kwargs):
            real(name,*args,**kwargs)
            if name==self.db.name: raise OSError('uncertain return')
        with patch.object(a.os,'unlink',fail): r=self.run_archive()
        self.assertEqual(r['status'],'retirement_uncertain'); self.assertEqual(r['original'],'absent')
    def test_failed_verified_journal(self):
        real=a._journal
        def fail(fd,name,data):
            if name=='verified.json': raise OSError('injected failure')
            return real(fd,name,data)
        with patch.object(a,'_journal',fail): r=self.run_archive()
        self.retained(r); self.assertTrue(r['archive_verified']); self.assertFalse(r['journal_durable'])
    def test_failed_result_journal(self):
        real=a._journal
        def fail(fd,name,data):
            if name=='retired.json': raise OSError('injected failure')
            return real(fd,name,data)
        with patch.object(a,'_journal',fail): r=self.run_archive()
        self.assertEqual(r['status'],'retirement_uncertain'); self.assertEqual(r['original'],'absent'); self.assertTrue((self.out/'operation'/'verified.json').exists())
    def test_failed_after_unlink(self):
        def hook(s):
            if s=='after_unlink': raise OSError('injected durability failure')
        r=self.run_archive(hook); self.assertEqual(r['status'],'retirement_uncertain'); self.assertEqual(r['original'],'absent')
    def test_sidecars(self):
        for suffix in ('-wal','-shm','-journal'):
            side=Path(str(self.db)+suffix); side.write_bytes(b'present'); self.plan['source_parent_identity']=a.identity(self.src.stat(),True); self.retained(self.run_archive()); side.unlink()
        self.assertFalse((self.out/'operation').exists())
    def test_sidecar_before_retire(self):
        def hook(s):
            if s=='before_retire': Path(str(self.db)+'-wal').write_bytes(b'present')
        self.retained(self.run_archive(hook))
    def test_malformed_plans(self):
        for field,value in (('extra',1),('max_seconds',True),('source',str(self.src)+'/../source/closed.sqlite'),('max_input_bytes',-1)):
            p=copy.deepcopy(self.plan); p[field]=value; self.retained(a.archive_backup(p))
    def test_space_time_budgets(self):
        with patch.object(a.os,'fstatvfs',return_value=type('V',(),{'f_bavail':0,'f_frsize':4096})()): self.retained(self.run_archive())
        with patch.object(a.time,'monotonic',side_effect=[0,30]): self.retained(self.run_archive())
    def test_sqlite_corrupt(self):
        self.db.write_bytes(b'not sqlite'*100); self.db.chmod(0o600); self.plan['source_identity']=a.identity(self.db.stat()); self.plan['sha256']=hashlib.sha256(self.db.read_bytes()).hexdigest(); self.retained(self.run_archive())
    def test_restore_changed_archive(self):
        r=self.run_archive(); m=r['mapping']
        with Path(m['archive_path']).open('ab') as f: f.write(b'changed')
        with self.assertRaises(a.Refused): self.restore_mapping(m,str(self.restore/'new.sqlite'))

    def test_restore_requires_separate_destination_and_budgets(self):
        r=self.run_archive(); m=r['mapping']
        for destination in (m['original_path'],m['archive_path']):
            with self.assertRaises(a.Refused): self.restore_mapping(m,destination)
        for field,value in (('max_seconds',True),('max_output_bytes',100),('max_archive_bytes',100),('reserve_bytes',-1)):
            with self.assertRaises(a.Refused): self.restore_mapping(m,str(self.restore/'new.sqlite'),**{field:value})
        with patch.object(a.os,'fstatvfs',return_value=type('V',(),{'f_bavail':0,'f_frsize':4096})()):
            with self.assertRaises(a.Refused): self.restore_mapping(m,str(self.restore/'new.sqlite'))
        self.assertFalse((self.restore/'new.sqlite').exists())
    def test_restore_strict_mapping(self):
        m=self.run_archive()['mapping']
        for field,value in (('archive_bytes',True),('original_bytes',-1),('original_path','relative'),('sqlite_integrity','failed'),('extra',1)):
            bad=copy.deepcopy(m); bad[field]=value
            with self.assertRaises(a.Refused): self.restore_mapping(bad,str(self.restore/'new.sqlite'))
        self.assertFalse((self.restore/'new.sqlite').exists())
    def test_archive_parent_contents_changed(self):
        def hook(s):
            if s=='before_retire': (self.out/'new-entry').write_bytes(b'x')
        self.retained(self.run_archive(hook))
    def test_operation_contents_changed(self):
        def hook(s):
            if s=='before_retire': (self.out/'operation'/'new-entry').write_bytes(b'x')
        self.retained(self.run_archive(hook))
    def test_source_parent_change_during_final_hash(self):
        real=a._hash_fd; source_hash_calls=0
        def change(fd,deadline,maximum):
            nonlocal source_hash_calls
            output=real(fd,deadline,maximum)
            if os.fstat(fd).st_ino==self.plan['source_identity']['ino']:
                source_hash_calls+=1
                if source_hash_calls==2: (self.src/'new-entry').write_bytes(b'x')
            return output
        with patch.object(a,'_hash_fd',change): self.retained(self.run_archive())
    def test_actual_parent_fsync_failure(self):
        real=os.fsync
        def fail(fd):
            if os.fstat(fd).st_ino==self.plan['source_parent_identity']['ino']: raise OSError('injected source directory fsync failure')
            return real(fd)
        with patch.object(a.os,'fsync',fail): r=self.run_archive()
        self.assertEqual(r['status'],'retirement_uncertain'); self.assertEqual(r['original'],'absent')
    def test_actual_verified_journal_fsync_failure(self):
        real_new=a._new; real_sync=os.fsync; verified_inode=None
        def capture(fd,name):
            nonlocal verified_inode
            out=real_new(fd,name)
            if name=='verified.json': verified_inode=os.fstat(out).st_ino
            return out
        def fail(fd):
            if verified_inode is not None and os.fstat(fd).st_ino==verified_inode: raise OSError('injected verified journal fsync failure')
            return real_sync(fd)
        with patch.object(a,'_new',capture), patch.object(a.os,'fsync',fail): r=self.run_archive()
        self.retained(r); self.assertFalse(r['journal_durable']); self.assertIn('mapping',r)

    def test_restore_sidecars(self):
        m=self.run_archive()['mapping']; destination=self.restore/'new.sqlite'
        for suffix in ('-wal','-shm','-journal'):
            sibling=Path(str(destination)+suffix); sibling.write_bytes(b'present')
            with self.assertRaises(a.Refused): self.restore_mapping(m,str(destination))
            self.assertFalse(destination.exists()); sibling.unlink()
    def test_restore_sidecar_created_during_verification(self):
        m=self.run_archive()['mapping']; destination=self.restore/'new.sqlite'; real=a._verify_gzip
        def create_sidecar(*args,**kwargs):
            output=real(*args,**kwargs); Path(str(destination)+'-wal').write_bytes(b'present'); return output
        with patch.object(a,'_verify_gzip',create_sidecar):
            with self.assertRaises(a.Refused): self.restore_mapping(m,str(destination))
        self.assertTrue(destination.exists()); self.assertEqual(destination.read_bytes(),self.original)

    def test_restore_parent_renamed_during_final_hash(self):
        m=self.run_archive()['mapping']; destination=self.restore/'new.sqlite'; real=a._hash_fd; calls=0
        def change(*args,**kwargs):
            nonlocal calls
            output=real(*args,**kwargs); calls+=1
            if calls==2: self.restore.rename(self.root/'restores-kept'); self.restore.mkdir(mode=0o700)
            return output
        with patch.object(a,'_hash_fd',change):
            with self.assertRaises(a.Refused): self.restore_mapping(m,str(destination))
        self.assertFalse(destination.exists()); self.assertTrue((self.root/'restores-kept'/'new.sqlite').exists())
    def test_restore_archive_mode_changed_during_final_hash(self):
        m=self.run_archive()['mapping']; real=a._hash_fd; calls=0
        def change(*args,**kwargs):
            nonlocal calls
            output=real(*args,**kwargs); calls+=1
            if calls==2: Path(m['archive_path']).chmod(0o644)
            return output
        with patch.object(a,'_hash_fd',change):
            with self.assertRaises(a.Refused): self.restore_mapping(m,str(self.restore/'new.sqlite'))

if __name__=='__main__': unittest.main(verbosity=2)
