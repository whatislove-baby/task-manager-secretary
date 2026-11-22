"""Exercise our shim against patched ChatOpenRouter parent streams, no network."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from httpx import ConnectError, RemoteProtocolError
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openrouter import ChatOpenRouter

from task_backend.agent_openrouter import FormatPreservingChatOpenRouter


def chunk(text='', **kwargs):
    return ChatGenerationChunk(message=AIMessageChunk(content=text, **kwargs))


class AsyncProviderRetry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model=FormatPreservingChatOpenRouter(model='fixture/model',api_key='fixture-not-a-real-key',max_retries=0)
        self.messages=[HumanMessage(content='synthetic request')]

    async def test_zero_chunk_transport_failure_retries_once_and_preserves_metadata(self):
        calls=[];first=chunk('one',additional_kwargs={'reasoning_details':[{'index':0,'format':'deepseek-v1','text':'a'}],'other':'keep'})
        second=chunk('two',additional_kwargs={'reasoning_details':[{'index':0,'format':'deepseek-v1','text':'b','signature':'signature'}]},
                     tool_call_chunks=[{'name':'read_file','args':'{"file_id":"a"}','id':'call-a','index':0}],response_metadata={'finish_reason':'tool_calls'})
        async def parent(model,messages,**kwargs):
            calls.append((messages,kwargs))
            if len(calls)==1:raise RemoteProtocolError('fixture disconnect')
            yield first;yield second
        callback=SimpleNamespace(on_llm_new_token=AsyncMock())
        with patch.object(ChatOpenRouter,'_astream',parent),patch('task_backend.agent_openrouter.asyncio.sleep',new_callable=AsyncMock) as delay:
            result=[c async for c in self.model._astream(self.messages,stop=['END'],run_manager=callback,temperature=0)]
        self.assertEqual(len(calls),2);delay.assert_awaited_once_with(0.25)
        for messages,kwargs in calls:
            self.assertEqual(messages,self.messages);self.assertEqual(kwargs,{'stop':['END'],'run_manager':None,'temperature':0})
        self.assertEqual([c.text for c in result],['one','two'])
        self.assertEqual(result[0].message.additional_kwargs,first.message.additional_kwargs)
        self.assertNotIn('format',result[1].message.additional_kwargs['reasoning_details'][0])
        self.assertEqual(result[1].message.additional_kwargs['reasoning_details'][0]['signature'],'signature')
        self.assertEqual(result[1].message.tool_call_chunks,second.message.tool_call_chunks)
        self.assertEqual(result[1].message.response_metadata,second.message.response_metadata)
        self.assertIn('format',second.message.additional_kwargs['reasoning_details'][0])
        self.assertEqual(callback.on_llm_new_token.await_count,2)

    async def test_any_chunk_commits_stream_and_prevents_retry(self):
        cases=[chunk('partial'),chunk(''),chunk('',usage_metadata={'input_tokens':3,'output_tokens':0,'total_tokens':3}),
               chunk('',tool_call_chunks=[{'name':'run_learning','args':'{"file_id":','id':'call-a','index':0}])]
        for emitted in cases:
            with self.subTest(message=emitted.message):
                calls=[];received=[]
                async def parent(model,messages,**kwargs):
                    calls.append(1);yield emitted;raise RemoteProtocolError('fixture disconnect')
                with patch.object(ChatOpenRouter,'_astream',parent),patch('task_backend.agent_openrouter.asyncio.sleep',new_callable=AsyncMock) as delay:
                    with self.assertRaises(RemoteProtocolError):
                        async for c in self.model._astream(self.messages):received.append(c)
                self.assertEqual(len(calls),1);self.assertEqual(received,[emitted]);delay.assert_not_awaited()

    async def test_repeated_failure_is_bounded_and_original_error_propagates(self):
        errors=[ConnectError('first'),RemoteProtocolError('second')];calls=[]
        async def parent(model,messages,**kwargs):
            calls.append(1);raise errors[len(calls)-1]
            yield  # pragma: no cover -- declare this stub an async generator
        with patch.object(ChatOpenRouter,'_astream',parent),patch('task_backend.agent_openrouter.asyncio.sleep',new_callable=AsyncMock) as delay:
            with self.assertRaises(RemoteProtocolError) as raised:
                _=[c async for c in self.model._astream(self.messages)]
        self.assertIs(raised.exception,errors[1]);self.assertEqual(len(calls),2);delay.assert_awaited_once_with(0.25)

    async def test_cancel_during_parent_stream_is_never_retried(self):
        calls=[]
        async def parent(model,messages,**kwargs):
            calls.append(1);raise asyncio.CancelledError()
            yield
        with patch.object(ChatOpenRouter,'_astream',parent),patch('task_backend.agent_openrouter.asyncio.sleep',new_callable=AsyncMock) as delay:
            with self.assertRaises(asyncio.CancelledError):
                _=[c async for c in self.model._astream(self.messages)]
        self.assertEqual(len(calls),1);delay.assert_not_awaited()

    async def test_cancel_during_retry_delay_prevents_second_request(self):
        calls=[]
        async def parent(model,messages,**kwargs):
            calls.append(1);raise ConnectError('fixture connect')
            yield
        with patch.object(ChatOpenRouter,'_astream',parent),patch('task_backend.agent_openrouter.asyncio.sleep',new_callable=AsyncMock,side_effect=asyncio.CancelledError()) as delay:
            with self.assertRaises(asyncio.CancelledError):
                _=[c async for c in self.model._astream(self.messages)]
        self.assertEqual(len(calls),1);delay.assert_awaited_once_with(0.25)

    async def test_application_error_is_not_a_retryable_transport_error(self):
        calls=[]
        async def parent(model,messages,**kwargs):
            calls.append(1);raise ValueError('provider application error')
            yield
        with patch.object(ChatOpenRouter,'_astream',parent),patch('task_backend.agent_openrouter.asyncio.sleep',new_callable=AsyncMock) as delay:
            with self.assertRaises(ValueError):_=[c async for c in self.model._astream(self.messages)]
        self.assertEqual(len(calls),1);delay.assert_not_awaited()


class SyncProviderRetry(unittest.TestCase):
    def setUp(self):
        self.model=FormatPreservingChatOpenRouter(model='fixture/model',api_key='fixture-not-a-real-key',max_retries=0)
        self.messages=[HumanMessage(content='synthetic request')]

    def test_prechunk_retry_keeps_callbacks_and_metadata(self):
        calls=[];expected=chunk('ok',response_metadata={'finish_reason':'stop'})
        def parent(model,messages,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:raise ConnectError('fixture connect')
            yield expected
        callback=SimpleNamespace(on_llm_new_token=Mock())
        with patch.object(ChatOpenRouter,'_stream',parent),patch('task_backend.agent_openrouter.time.sleep') as delay:
            result=list(self.model._stream(self.messages,run_manager=callback,seed=5))
        self.assertEqual(result,[expected]);self.assertEqual(len(calls),2);delay.assert_called_once_with(0.25)
        self.assertEqual(calls[0],{'stop':None,'run_manager':None,'seed':5});self.assertEqual(callback.on_llm_new_token.call_count,1)

    def test_partial_stream_does_not_retry(self):
        calls=[];expected=chunk('',tool_call_chunks=[{'name':'create_mail_draft','args':'{','id':'call-1','index':0}])
        def parent(model,messages,**kwargs):
            calls.append(1);yield expected;raise RemoteProtocolError('after tool delta')
        with patch.object(ChatOpenRouter,'_stream',parent),patch('task_backend.agent_openrouter.time.sleep') as delay:
            stream=self.model._stream(self.messages);self.assertEqual(next(stream),expected)
            with self.assertRaises(RemoteProtocolError):next(stream)
        self.assertEqual(len(calls),1);delay.assert_not_called()

    def test_repeated_failure_is_bounded(self):
        calls=[]
        def parent(model,messages,**kwargs):
            calls.append(1);raise RemoteProtocolError('fixture disconnect')
            yield
        with patch.object(ChatOpenRouter,'_stream',parent),patch('task_backend.agent_openrouter.time.sleep') as delay:
            with self.assertRaises(RemoteProtocolError):list(self.model._stream(self.messages))
        self.assertEqual(len(calls),2);delay.assert_called_once_with(0.25)


if __name__=='__main__':unittest.main()
