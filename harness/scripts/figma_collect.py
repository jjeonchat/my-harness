"""Read-only Figma REST collector; never writes to the remote design."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from core import Invalid, NODE_ROLES, digest, input_data, now, number, object_, read, require, text

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Invalid('Figma API 리다이렉트 거부')

def fetch(file_key, ids, token):
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]+',file_key)), '유효하지 않은 Figma file key')
    query=urllib.parse.urlencode({'ids':','.join(ids)})
    url=f'https://api.figma.com/v1/files/{file_key}/nodes?{query}'
    req=urllib.request.Request(url,headers={'X-Figma-Token':token})
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as response:
            raw=response.read(25_000_001)
            require(len(raw)<=25_000_000,'응답이 너무 큽니다. 프레임 범위를 줄이세요.')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise Invalid(f'Figma 조회 실패 HTTP {exc.code}; 인증/권한/사용량을 확인하세요.') from None
    except (urllib.error.URLError,TimeoutError,json.JSONDecodeError):
        raise Invalid('Figma 네트워크 또는 응답 오류. 이전 증거를 통과로 재사용하지 않습니다.') from None

def color_hex(c):
    require(all(type(c.get(k)) in (int,float) and 0<=c[k]<=1 for k in ('r','g','b')), '유효하지 않은 색상')
    return '#'+''.join(f'{round(c[k]*255):02x}' for k in ('r','g','b'))

def flatten(node,roles,root_id):
    object_(node,['id','type','absoluteBoundingBox'],'Figma node')
    bounds=node['absoluteBoundingBox']
    number(bounds['width'],'width'); number(bounds['height'],'height')
    role='screen' if node['id']==root_id else roles.get(node['id'])
    if role is None and node['type']=='TEXT': role='text'
    require(role in NODE_ROLES, f"노드 {node['id']}의 역할을 manifest.roles에 지정하세요.")
    fills=[]
    for fill in node.get('fills',[]):
        if fill.get('visible',True) is False: continue
        fills.append({'type':fill['type'],'color':color_hex(fill['color']) if fill['type']=='SOLID' else None,
                      'opacity':fill.get('opacity',1)*(fill.get('color',{}).get('a',1))})
    corners=node.get('rectangleCornerRadii',[node.get('cornerRadius',0)]*4)
    runs=[]
    if node['type']=='TEXT':
        base=node.get('style')
        require(isinstance(base,dict),'텍스트 스타일 누락')
        styles=[base]
        for override in node.get('styleOverrideTable',{}).values(): styles.append(dict(base,**override))
        for style in styles:
            require('letterSpacing' in style,'letterSpacing 조회 누락')
            runs.append({'letter_spacing':style['letterSpacing'],'text_case':style.get('textCase','ORIGINAL')})
    row={'id':node['id'],'type':node['type'],'role':role,'width':bounds['width'],'height':bounds['height'],
         'effects':[{'type':e['type'],'visible':e.get('visible',True)} for e in node.get('effects',[])],
         'fills':fills,'corners':corners,'text_runs':runs}
    rows=[row]
    for child in node.get('children',[]): rows.extend(flatten(child,roles,root_id))
    return rows

def normalize(payload,manifest,concept_version=None):
    object_(payload,['nodes','version'],'Figma API response')
    text(payload['version'],'Figma file version')
    require(not payload.get('err'), 'Figma API 오류')
    frames=[]
    for requested in manifest['frames']:
        item=payload['nodes'].get(requested['node_id'])
        require(item is not None, f"Figma node 누락: {requested['node_id']}")
        doc=item['document']
        require(doc['id']==requested['node_id'],'Figma node ID 불일치')
        require(doc['type'] in ('FRAME','COMPONENT','INSTANCE'), '화면 루트는 프레임/컴포넌트/인스턴스여야 합니다.')
        rows=flatten(doc,manifest['roles'],doc['id'])
        frames.append({'screen_id':requested['screen_id'],'id':doc['id'],
                       'width':rows[0]['width'],'height':rows[0]['height'],'nodes':rows})
    result={'manifest':manifest,'source':{'kind':'figma-rest','file_key':manifest['file_key'],
            'version':payload['version'],'collected_at':now(),'complete':True,'manifest_hash':digest(manifest)},'frames':frames}
    if concept_version is not None: result['concept_version']=concept_version
    return result

def collect(run,stage):
    from harness import save_state, state, write
    s=state(run)
    require(s['active_stage']==stage,'해당 디자인 단계를 먼저 begin 하세요.')
    require(input_data(run)['mode']=='live','fixture 실행은 원격 Figma를 조회하지 않습니다.')
    rel='03-concept/key-screens.json' if stage==3 else '04-design/screens.json'
    path=run/rel
    require(path.resolve().is_relative_to(run.resolve()),'수집 경로 이탈')
    doc=read(path); manifest=object_(doc.get('manifest'),['file_key','frames','roles'],'manifest')
    require(isinstance(manifest['frames'],list) and bool(manifest['frames']),'프레임 목록 필요')
    require(isinstance(manifest['roles'],dict),'노드 역할 매핑 필요')
    token=os.environ.get('FIGMA_ACCESS_TOKEN','')
    # Invalidate any old receipt before trying a new network request.
    s.setdefault('collections',{}).pop(str(stage),None); save_state(run,s)
    require(bool(token),'FIGMA_ACCESS_TOKEN 환경변수가 필요합니다. 토큰은 파일/채팅에 기록하지 마세요.')
    ids=[text(x['node_id'],'node_id') for x in manifest['frames']]
    require(all(re.fullmatch(r'[0-9]+:[0-9]+',x) for x in ids),'유효하지 않은 node ID')
    payload=fetch(manifest['file_key'],ids,token)
    result=normalize(payload,manifest,doc.get('concept_version') if stage==3 else None)
    write(path,result)
    s.setdefault('collections',{})[str(stage)]={'hash':digest(result),'collected_at':result['source']['collected_at']}
    save_state(run,s)
    return {'collected':len(result['frames']),'path':rel,'version':result['source']['version']}
