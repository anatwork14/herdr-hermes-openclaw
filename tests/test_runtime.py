import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from herdctl import memory as m
from herdctl import runtime as rt

class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cx=m.connection(Path(self.tmp.name)/'test.db')
        self.cx.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?,?)",('st_codex','starlight-tempo','codex','w1:p1','w1','/tmp/wt',0))
        self.cx.commit()
        self.cfg={'herdr_binary':'herdr','openclaw_binary':'openclaw','projects':{
            'starlight-tempo':{'repo':'/tmp/fake','discord_channel_id':'123456789012345678'},
            'other':{'repo':'/tmp/fake','discord_channel_id':'123456789012345679'}}}
    def tearDown(self): self.cx.close(); self.tmp.cleanup()
    def test_cross_project_prompt_denied(self):
        with self.assertRaises(rt.RuntimeError_):
            rt.prompt_agent(self.cfg,self.cx,project='other',agent='st_codex',text='hello',yes=True)
    def test_requires_confirmation(self):
        with self.assertRaises(ValueError):
            rt.prompt_agent(self.cfg,self.cx,project='starlight-tempo',agent='st_codex',text='hi')
    @patch('herdctl.runtime.herdr')
    def test_prompt_checks_pane(self, fake):
        fake.return_value={'result':{'agent':{'pane_id':'w9:p9'}}}
        with self.assertRaises(rt.RuntimeError_):
            rt.prompt_agent(self.cfg,self.cx,project='starlight-tempo',agent='st_codex',text='hi',yes=True)
    @patch('herdctl.runtime.herdr')
    def test_prompt_sends_correct_cli(self, fake):
        fake.side_effect=[{'result':{'agent':{'pane_id':'w1:p1'}}},{'ok':True},{'result':{'agent':{'pane_id':'w1:p1'}}}]
        rt.prompt_agent(self.cfg,self.cx,project='starlight-tempo',agent='st_codex',text='hello',yes=True)
        self.assertEqual(fake.call_args_list[1].args[1],['agent','prompt','w1:p1','hello'])
    @patch('herdctl.runtime.herdr')
    def test_unstructured_agent_get_denied(self, fake):
        fake.return_value='unknown output'
        with self.assertRaises(rt.RuntimeError_):
            rt.prompt_agent(self.cfg,self.cx,project='starlight-tempo',agent='st_codex',text='hi',yes=True)
    @patch('herdctl.runtime.command')
    def test_discord_target_fixed(self, fake):
        fake.return_value='sent'
        rt.notify(self.cfg,'starlight-tempo','Ready',yes=True)
        self.assertEqual(fake.call_args.args[0][-2:],['--message','Ready'])
        self.assertIn('channel:123456789012345678',fake.call_args.args[0])

if __name__=='__main__': unittest.main()
