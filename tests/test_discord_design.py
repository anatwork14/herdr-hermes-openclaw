import json
import tempfile
import unittest
from pathlib import Path

from herdctl import discord_design
from herdctl.config import ConfigError

class TestDiscordDesign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'config').mkdir()
        origin = Path(__file__).resolve().parents[1]/'config'
        for name in ('discord_layout.example.json','token_policy.example.json'):
            (self.root/'config'/name).write_bytes((origin/name).read_bytes())
        self.cfg = {'projects': {'demo': {'repo':'/tmp/demo','discord_channel_id':'123456789012345678'}}}
    def tearDown(self): self.tmp.cleanup()
    def test_policy_budget(self):
        a = discord_design.budget(self.root,'supervisor')
        b = discord_design.budget(self.root,'supervisor',True)
        self.assertGreater(a['memory_context_budget_estimate'],b['memory_context_budget_estimate'])
    def test_missing_ids_and_one_token(self):
        x = discord_design.discord_plan(self.root,self.cfg)
        self.assertEqual(x['bot_accounts'],1)
        self.assertIn('guild_id',x['missing_fields'])
        self.assertEqual(x['project_channels']['demo']['channel_id'],'123456789012345678')
    def test_duplicate_channel_id_rejected(self):
        layout = json.loads((self.root/'config'/'discord_layout.example.json').read_text())
        layout['control_channels']['alerts']['channel_id']='123456789012345678'
        (self.root/'config'/'discord_layout.example.json').write_text(json.dumps(layout))
        with self.assertRaises(ConfigError):
            discord_design.discord_plan(self.root,self.cfg)
    def test_invalid_owner_id_rejected(self):
        layout = json.loads((self.root/'config'/'discord_layout.example.json').read_text())
        layout['owner_user_ids']=['NOT_A_DISCORD_USER_ID']
        (self.root/'config'/'discord_layout.example.json').write_text(json.dumps(layout))
        with self.assertRaises(ConfigError):
            discord_design.discord_plan(self.root,self.cfg)
    def test_trusted_thread_parent_routing(self):
        x = discord_design.project_for_channel(self.cfg,'223456789012345678','123456789012345678')
        self.assertEqual(x['project'],'demo')
        self.assertEqual(x['via'],'trusted_thread_parent')
    def test_unmapped_thread_rejected(self):
        with self.assertRaises(ConfigError):
            discord_design.project_for_channel(self.cfg,'223456789012345678')
    def test_bad_parent_rejected(self):
        with self.assertRaises(ConfigError):
            discord_design.project_for_channel(self.cfg,'223456789012345678','not-metadata')
    def test_unknown_role_rejected(self):
        with self.assertRaises(ConfigError):
            discord_design.budget(self.root,'unlimited')

if __name__=='__main__': unittest.main()
