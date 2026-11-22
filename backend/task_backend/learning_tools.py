"""Agent mixin: draft, retrieve and run approved knowledge; never approve it."""
from uuid import NAMESPACE_URL, uuid5
import json
from .learning import compact


class LearningAgentTools:
    def propose_learning(self,value,*,call_id):
        value=json.loads(compact(value))
        stable=str(uuid5(NAMESPACE_URL,'task-manager-agent:'+self.execution_id+':'+str(call_id)))
        teaching=self.service.learning.teaching_for_call(stable,value,self.conversation_id,self.execution_id)
        result=self._once('propose_learning',call_id,dict(value=value,teaching=teaching),lambda stable,created:self.service.learning.propose(value,proposal_id=stable,teaching=teaching))
        self._artifact('learning_draft',result['id'],result['title'])
        return result

    def search_learning(self,query='',sender='',scope_id='',status='approved'):
        result=self.service.learning.search(query,sender,scope_id,status)
        for item in result:
            item['canRun']=item['status']=='approved' and item['proposal']['kind']=='table_workflow'
            self._source('learning' if item['status']=='approved' else 'learning_draft',item['id'],item['title'],item['proposal']['instruction'])
        return dict(methods=result,count=len(result),notice='默认仅检索当前已批准方法；draft/all 可供纠正和比较，不能执行未批准、停用或被替代版本。From 地址仅为邮件声明身份。')

    def get_learning(self,id):
        result=self.service.learning.get(id)
        self._source('learning' if result['status']=='approved' else 'learning_draft',result['id'],result['title'],result['proposal']['instruction'])
        return dict(result,canRun=result['status']=='approved' and result['proposal']['kind']=='table_workflow',
                    executionNotice='只有当前已批准的 table_workflow 可以运行；读取草稿、旧版或停用版本仅用于比较与提出新修订，不等于批准。')

    def run_learning(self,version_id,file_id,name,scope_id,source_mail_id='',*,call_id):
        # Do not use the generic cached _once result here: every replay must
        # re-check approval, scope and file hashes, including after revocation.
        stable=str(uuid5(NAMESPACE_URL,'task-manager-learning-tool:'+self.execution_id+':'+str(call_id)))
        result=self.service.learning.run(version_id,file_id,name,scope_id,source_mail_id,run_id=stable)
        self._source('learning',version_id,'已批准方法 v'+str(result['audit']['approvedVersion']))
        self._artifact('file',result['file']['id'],result['file']['name'])
        return result
