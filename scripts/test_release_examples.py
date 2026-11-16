import importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('examples',Path(__file__).with_name('release_examples.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ExamplesTest(unittest.TestCase):
 def test_first_changed_entry_and_whitespace(self):
  old=[{'n':1,'fr':'Identique'},{'n':2,'fr':'Bonjour monde'},{'n':3,'fr':'Avant'}]
  new=[{'n':1,'fr':'Identique'},{'n':2,'fr':'Bonjour ami'},{'n':3,'fr':'Après'}]
  result=module.example(old,new)
  self.assertEqual(result['key'],2)
  self.assertEqual(result['segments'],[{'text':'Bonjour ','changed':False},{'text':'ami','changed':True}])
  self.assertEqual(result['removed'],['monde'])
  self.assertEqual(module.segments('Texte','   Texte')[0][0],{'text':'   ','changed':True})
 def test_long_book_context_keeps_all_changes_and_deletions(self):
  prefix='Un passage ancien sans modification. '*40
  middle='Une longue transition identique. '*40
  suffix='Fin de page inchangée. '*40
  result=module.example([{'fichier':'t_book12','livre':1,'page':6,'fr':prefix+'ancien'+middle+'retiré'+suffix}],
                        [{'fichier':'t_book12','livre':1,'page':6,'fr':prefix+'nouveau'+middle+suffix}])
  rendered=''.join(p['text'] for p in result['segments'])
  self.assertIn('nouveau',rendered)
  self.assertIn('…',rendered)
  self.assertLess(len(rendered),600)
  self.assertIn('ancien',''.join(result['removed']))
  self.assertIn('retiré',''.join(result['removed']))
  self.assertEqual(result['key'],'livre 2, page 6')

 def test_report_contains_every_changed_entry(self):
  old=[{'n':1,'fr':'ancien un'},{'n':2,'fr':'ancien deux'}]
  new=[{'n':1,'fr':'nouveau un'},{'n':2,'fr':'nouveau deux'}]
  self.assertEqual([c['key'] for c in module.examples(old,new)],[1,2])

 def test_release_tag_comparison_and_image_asset(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   def git(*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
   git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
   target=root/'data/scripts/test.json';target.parent.mkdir(parents=True)
   target.write_text(json.dumps([{'n':1,'fr':'Avant'}]),encoding='utf-8')
   git('add','.');git('commit','-m','old');git('tag','v1')
   target.write_text(json.dumps([{'n':1,'fr':'Après'}]),encoding='utf-8')
   image=root/'data/images/title.png';image.parent.mkdir();image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
   git('add','.');git('commit','-m','new')
   result=module.collect(root,'v1',root/'out')
   self.assertEqual(len(result['changes']),2)
   picture=next(c for c in result['changes'] if c['kind']=='image')
   self.assertEqual((root/'out'/picture['image_asset']).read_bytes(),image.read_bytes())
   self.assertEqual(next(c for c in result['changes'] if c['kind']=='text')['removed'],['Avant'])
if __name__=='__main__':unittest.main()
