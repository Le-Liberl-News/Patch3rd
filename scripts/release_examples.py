"""French changes grouped by file, compared with the previous release.
No Japanese/English game text is included. PNG previews are separate assets.
"""
from pathlib import Path
import argparse,difflib,json,re,shutil,subprocess

def segments(before,after):
 a=re.findall(r'\s+|[^\s]+',before);b=re.findall(r'\s+|[^\s]+',after)
 result=[];removed=[]
 ops=difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes()
 for index,(op,i,j,k,l) in enumerate(ops):
  if op in ('delete','replace'):removed.append(''.join(a[i:j]))
  if op=='delete':continue
  text=''.join(b[k:l])
  if op=='equal' and len(text)>200:
   # Keep context beside each change, including pure deletions. Never let an
   # unchanged page opening consume Discord's limit before the changed words.
   left=text[:80] if index>0 else ''
   right=text[-80:] if index<len(ops)-1 else ''
   text=left+' … '+right
  result.append({'text':text,'changed':op!='equal'})
 return result,removed

def identity(row):
 return (row.get('n'),row.get('cle'),row.get('type'),row.get('fichier'),row.get('livre'),row.get('page'))

def examples(before,after):
 old={identity(row):row for row in before};new={identity(row):row for row in after};result=[]
 for key,row in new.items():
  was=str(old.get(key,{}).get('fr') or '');now=str(row.get('fr') or '')
  if was!=now:
   diff,removed=segments(was,now)
   result.append({'key':(f"livre {int(row['livre'])+1}, page {row['page']}" if 'livre' in row else row.get('n',row.get('cle',row.get('page')))), 'segments':diff,'removed':removed})
 for key,row in old.items():
  if key not in new:
   result.append({'key':(f"livre {int(row['livre'])+1}, page {row['page']}" if 'livre' in row else row.get('n',row.get('cle',row.get('page')))), 'segments':[],'removed':[row['fr']]})
 return result

def example(before,after):
 # Compatibility for older callers: the website uses all changes.
 found=examples(before,after)
 if not found:return None
 result=found[0]
 old={identity(row):row for row in before}
 for row in after:
  if str(old.get(identity(row),{}).get('fr') or '')!=str(row.get('fr') or ''):
   return dict(result,before=str(old.get(identity(row),{}).get('fr') or ''),after=str(row.get('fr') or ''))
 return dict(result,before=result['removed'][0],after='')

def collect(root,previous,out):
 out.mkdir(parents=True,exist_ok=True);changes=[]
 if not previous:return {'schema':1,'previous':None,'changes':[]}
 names=subprocess.run(['git','diff','--name-only',previous,'--','data'],cwd=root,check=True,capture_output=True,text=True).stdout.splitlines()
 for path in names:
  current=root/path
  old=subprocess.run(['git','show',previous+':'+path],cwd=root,capture_output=True)
  before=old.stdout if old.returncode==0 else b''
  if path.startswith('data/images/'):
   if not current.is_file() or current.stat().st_size>8*1024*1024:continue
   asset='change-preview-'+str(len(changes)+1)+'.png';shutil.copyfile(current,out/asset)
   changes.append({'kind':'image','file':path[len('data/images/'):],'image_asset':asset});continue
  if not path.endswith('.json'):continue
  prior=json.loads(before.decode('utf-8')) if before else []
  latest=json.loads(current.read_text(encoding='utf-8')) if current.exists() else []
  if not isinstance(prior,list) or not isinstance(latest,list):continue
  if path=='data/livres.json':
   for filename in sorted({r['fichier'] for r in prior+latest}):
    for change in examples([r for r in prior if r['fichier']==filename],[r for r in latest if r['fichier']==filename]):
     changes.append({'kind':'text','file':'livres/'+filename,**change})
   continue
  for change in examples(prior,latest):changes.append({'kind':'text','file':path[len('data/'):],**change})
 return {'schema':1,'previous':previous,'changes':changes}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--previous',default='');p.add_argument('--out',type=Path,required=True);args=p.parse_args()
 root=Path(__file__).resolve().parents[1];result=collect(root,args.previous,args.out.parent/'change-previews')
 args.out.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');print('Release examples:',len(result['changes']))
