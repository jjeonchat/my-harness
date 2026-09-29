"""Synthetic fixtures only. Never represents a real Figma collection or user approval."""
from copy import deepcopy
from core import digest, now
from harness import write

INPUT={'flow':'내 자산 등록 → 판매 신청 (검증용 가상 데이터)', 'screen_ids':['asset-create','sale-request'],
       'references':['fixture://reference-01'], 'scope':{'asset_registration':True,'sale_application':True,'not_applicable_reason':''}, 'mode':'fixture'}

def node(node_id,role='screen'):
    return {'id':node_id,'type':'FRAME','role':role,'width':390,'height':844,
            'effects':[],'fills':[],'corners':[24]*4,'text_runs':[]}

def snapshot(stage=3):
    frames=[]
    for index,screen_id in enumerate(INPUT['screen_ids']):
        nid=f'{stage}:{index+1}'; root=node(nid); button=node(nid+'0','button'); button['corners']=[9999]*4
        label=node(nid+'1','text'); label['type']='TEXT'; label['text_runs']=[{'letter_spacing':0,'text_case':'ORIGINAL'}]
        frames.append({'screen_id':screen_id,'id':nid,'width':390,'height':844,'nodes':[root,button,label]})
    manifest={'file_key':'FIXTURE-NOT-A-REAL-FILE','frames':[{'screen_id':f['screen_id'],'node_id':f['id']} for f in frames],
              'roles':{n['id']:n['role'] for f in frames for n in f['nodes']}}
    result={'manifest':manifest,'source':{'kind':'fixture','file_key':manifest['file_key'],'version':'fixture-v1',
             'collected_at':now(),'complete':True,'manifest_hash':digest(manifest)},'frames':frames}
    if stage==3: result['concept_version']='v1'
    return result

def seed(run):
    write(run/'01-research/references.json',[{'source':'fixture://reference-01','accessible':True,'summary':'가상 레퍼런스'}])
    write(run/'01-research/analysis.md','# 가상 분석\n검증용 자료이며 실제 레퍼런스가 아니다.\n')
    write(run/'02-spec/screen-spec.md','# 가상 화면 설계\n자산 등록과 판매 신청을 검증한다.\n')
    write(run/'02-spec/screens.json',{'screens':[{'id':s,'title':s} for s in INPUT['screen_ids']],
      'service':{'default_visibility':'private','sale_roles':['seller'],'states':['private','member_only','sale_requested','pending','approved'],
        'transitions':[{'from':'private','to':'member_only','user_selected':True,'reviewed_by_operator':False},
                       {'from':'private','to':'sale_requested','user_selected':True,'reviewed_by_operator':False},
                       {'from':'pending','to':'approved','user_selected':False,'reviewed_by_operator':True}]}})
    write(run/'03-concept/key-screens.json',snapshot(3))
    write(run/'04-design/tokens.json',{'tokens':[{'id':'radius-pill','name':'rounded.full','value':9999,'figma_variable_id':'fixture-variable'}]})
    write(run/'04-design/components.json',{'components':[{'id':'button','name':'button-primary','figma_node_id':'fixture-component'}]})
    write(run/'04-design/screens.json',snapshot(4))
