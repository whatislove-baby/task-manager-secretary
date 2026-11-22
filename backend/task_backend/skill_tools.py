"""Agent interface to versioned skill packages; approval is deliberately absent."""
import json
from uuid import NAMESPACE_URL, uuid5
from .learning import compact


class SkillAgentTools:
    def import_skill(self,file_id,scope,instruction='',parent_id=None,*,call_id):
        value=dict(file_id=file_id,scope=scope,instruction=instruction,parent_id=parent_id)
        stable=str(uuid5(NAMESPACE_URL,'task-manager-agent:'+self.execution_id+':'+str(call_id)))
        teaching=self.service.skills.teaching_for_call(stable,value,self.conversation_id,self.execution_id)
        result=self._once('import_skill',call_id,dict(value=value,teaching=teaching),
            lambda stable,created:self.service.skills.import_package(**value,proposal_id=stable,teaching=teaching))
        result=self.service.skills.get(result['id'])
        self._artifact('skill_draft',result['id'],result['title'])
        return result

    def propose_skill(self,value,*,call_id):
        value=json.loads(compact(value))
        stable=str(uuid5(NAMESPACE_URL,'task-manager-agent:'+self.execution_id+':'+str(call_id)))
        teaching=self.service.skills.teaching_for_call(stable,value,self.conversation_id,self.execution_id)
        result=self._once('propose_skill',call_id,dict(value=value,teaching=teaching),lambda stable,created:self.service.skills.propose(value,proposal_id=stable,teaching=teaching))
        # The journal caches a tool result, not authority to use an old status.
        result=self.service.skills.get(result['id'])
        self._artifact('skill_draft',result['id'],result['title'])
        return result

    def search_skills(self,query='',sender='',scope_id='',status='approved'):
        items=self.service.skills.search(query,sender,scope_id,status)
        result=[]
        for item in items:
            self._source('skill',item['id'],item['title'],item['proposal']['instruction'])
            result.append({key:item[key] for key in ('id','title','scopeId','scope','version','versionHash','status','canRun','canApprove','packagePath')})
            instruction=item['proposal']['instruction']
            result[-1].update(skillKind=item['proposal']['kind'],preview=instruction[:240],requiresLoad=True,instructionTruncated=len(instruction)>240)
        return dict(skills=result,count=len(result),notice='搜索仅用于发现，不表示全文已读。使用任何文字经验或脚本之前，必须对每个实际使用的技能调用 get_skill 读取完整 Markdown、适用范围和验证记录；240 字预览可能省略后续限制。仅当前已批准版本可复用，文字经验只可阅读参考。')

    def get_skill(self,id):
        result=self.service.skills.get(id)
        self._source('skill',result['id'],result['title'],result['proposal']['instruction'])
        return result

    def run_skill(self,version_id,inputs,scope_id,source_mail_id='',*,call_id):
        stable=str(uuid5(NAMESPACE_URL,'task-manager-agent-skill-run:'+self.execution_id+':'+str(call_id)))
        result=self.service.skills.run(version_id,inputs,scope_id,source_mail_id,run_id=stable)
        self._source('skill',version_id,'已批准技能 v'+str(result['audit']['approvedVersion']))
        for file in result['files']:self._artifact('file',file['id'],file['name'])
        return result
