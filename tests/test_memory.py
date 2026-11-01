import tempfile
import unittest
from pathlib import Path
from herdctl import memory as m

class TestMemory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cx = m.connection(Path(self.temp.name)/'memory.db')
    def tearDown(self):
        self.cx.close(); self.temp.cleanup()
    def new(self, project=None, scope=None, subject='baseline', content='Use MTP baseline for tests'):
        return m.add(self.cx, scope=scope or ('project' if project else 'global'), project=project,
                     type='decision',subject=subject,content=content,evidence='git:abc123')
    def test_isolation(self):
        self.new('starlight-tempo',subject='private-mtp',content='MTP benchmark')
        self.new('federated-learning-iot',subject='private-fbid',content='FBID dataset')
        self.new(subject='shared-policy',content='Always verify tests')
        st=m.search(self.cx,'MTP FBID tests',project='starlight-tempo')
        self.assertEqual({x['subject'] for x in st},{'private-mtp','shared-policy'})
        fl=m.search(self.cx,'MTP FBID tests',project='federated-learning-iot')
        self.assertEqual({x['subject'] for x in fl},{'private-fbid','shared-policy'})
    def test_no_global_read_of_project(self):
        self.new('starlight-tempo', content='Invisible project detail')
        self.new(content='Visible global detail')
        results=m.search(self.cx,'detail',project=None,global_only=True)
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['scope'],'global')
    def test_graph_traverse_scoped(self):
        a=self.new('starlight-tempo',subject='review')
        b=self.new('starlight-tempo',subject='test')
        c=self.new('federated-learning-iot',subject='other')
        m.link(self.cx,a,'DEPENDS_ON',b,'starlight-tempo')
        self.assertEqual(len(m.graph(self.cx,a,project='starlight-tempo')['nodes']),2)
        with self.assertRaises(m.MemoryError): m.link(self.cx,a,'USES',c,'starlight-tempo')
        with self.assertRaises(m.MemoryError): m.graph(self.cx,a,project='federated-learning-iot')
    def test_cross_scope_edge_denied(self):
        a=self.new('starlight-tempo')
        b=self.new()
        with self.assertRaises(m.MemoryError): m.link(self.cx,a,'USES',b,'starlight-tempo')
    def test_credentials_rejected(self):
        with self.assertRaises(m.MemoryError): self.new(content='api_key = abcde12345')
    def test_context_budget(self):
        for n in range(8): self.new('starlight-tempo',subject=f'benchmark-{n}',content='benchmark '+('x'*350))
        pack=m.context(self.cx,project='starlight-tempo',query='benchmark',budget=150)
        self.assertLessEqual(len(pack['text']),600)
        self.assertGreaterEqual(pack['records'],1)
    def test_task_reassignment_blocked(self):
        m.task_upsert(self.cx,'task-1','starlight-tempo','Run tests','queued')
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx,'task-1','federated-learning-iot','Steal task','working')
        self.assertEqual(self.cx.execute("SELECT project FROM task_state WHERE id='task-1'").fetchone()[0],'starlight-tempo')
    def test_fts_punctuation_safe(self):
        self.new('starlight-tempo',subject='MTP-v2')
        self.assertTrue(m.search(self.cx,'MTP-v2? "',project='starlight-tempo'))
    def test_fts_updates(self):
        a=self.new('starlight-tempo',subject='obsolete')
        self.cx.execute("UPDATE memories SET subject='replaced' WHERE id=?",(a,))
        self.cx.commit()
        self.assertEqual(len(m.search(self.cx,'replaced',project='starlight-tempo')),1)
    def test_evidence_required(self):
        with self.assertRaises(m.MemoryError):
            m.add(self.cx,scope='global',project=None,type='decision',subject='x',content='y',evidence='')

if __name__=='__main__': unittest.main()
