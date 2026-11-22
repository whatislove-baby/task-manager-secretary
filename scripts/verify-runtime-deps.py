"""Verify pinned dependency closure and exercise the shipped Agent runtime.

Uses only synthetic messages and a temporary SQLite database, no API or account.
Run with the target Python; suitable for relocated packaged Python with -I.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

# Explicit project script import also works under Python -I isolated mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from requirements_lock import normalized, read_lock


def verify_requirements(filename):
    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    pins, files = read_lock(filename)
    locked = {normalized(name): version for name, version in pins}
    installed = {}
    for name, version in pins:
        actual = importlib.metadata.version(name)
        if actual != version:
            raise RuntimeError(f'Dependency version mismatch: {name}, expected {version}, got {actual}')
        installed[normalized(name)] = actual
    # Check transitive metadata including explicitly requested dependency extras.
    pending = [(name, '') for name, _ in pins]
    seen = set()
    environment = default_environment()
    while pending:
        name, extra = pending.pop()
        key = (normalized(name), extra)
        if key in seen:
            continue
        seen.add(key)
        for raw in importlib.metadata.requires(name) or []:
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate({**environment, 'extra': extra}):
                continue
            dependency = normalized(req.name)
            if dependency not in locked:
                raise RuntimeError(f'Unpinned dependency required by {name}: {req.name}')
            if installed[dependency] not in req.specifier:
                raise RuntimeError(f'Incompatible dependency required by {name}: {raw}')
            pending.extend((req.name, value) for value in req.extras | {''})
    return dict(dependencies=installed, requirementsSha256={
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def smoke_agent():
    import os
    import sqlite3
    import tempfile
    from unittest.mock import patch
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from deepagents.backends.utils import create_file_data
    from deepagents.middleware.skills import SkillsMiddleware
    from deepagents.middleware.summarization import SummarizationMiddleware
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from langchain_openrouter import ChatOpenRouter
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from pydantic import Field

    class SyntheticModel(BaseChatModel):
        responses: list = Field(default_factory=list)
        visible_tools: list = Field(default_factory=list)
        seen_messages: list = Field(default_factory=list)

        @property
        def _llm_type(self):
            return 'runtime-offline-smoke'

        def bind_tools(self, tools, **kwargs):
            self.visible_tools.append([getattr(t, 'name', None) or
                (t.get('function', {}).get('name') or t.get('name')) for t in tools])
            return self

        def _generate(self, messages, **kwargs):
            self.seen_messages.append(list(messages))
            if not self.responses:
                raise RuntimeError('Unexpected synthetic model call')
            return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])

    @tool
    def read_fixture() -> str:
        """Read a synthetic runtime marker; no external data."""
        return 'course-fixture'

    def call(name, args, identity):
        return AIMessage(content='', tool_calls=[dict(name=name, args=args, id=identity)])

    skill_path = '/skills/runtime/SKILL.md'
    skill_text = ('---\nname: runtime\ndescription: Read the synthetic runtime marker\n'
                  'metadata:\n  include_tools: read_fixture\n---\nCall read_fixture after reading this skill.')
    with patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false', 'LANGCHAIN_TRACING': 'false'}), \
            patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), \
            patch('socket.socket.connect_ex', side_effect=AssertionError('Network forbidden')), \
            tempfile.TemporaryDirectory(prefix='tm-agent-runtime-') as directory:
        conn = sqlite3.connect(str(Path(directory) / 'state.sqlite'), check_same_thread=False)
        try:
            saver = SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
            backend = StateBackend()
            model = SyntheticModel(responses=[
                call('write_file', dict(file_path='/notes.md', content='course-fixture'), 'write'),
                call('read_file', dict(file_path='/notes.md'), 'read'),
                call('read_file', dict(file_path=skill_path), 'skill'),
                call('read_fixture', {}, 'fixture'),
                AIMessage(content='verified course-fixture'),
            ])
            agent = create_deep_agent(model, backend=backend, skills=['/skills/'],
                middleware=[SkillsMiddleware(backend=backend, sources=['/skills/'], tools=[read_fixture])],
                checkpointer=saver)
            config = {'configurable': {'thread_id': 'runtime-smoke'}, 'recursion_limit': 32}
            result = agent.invoke({'messages': [HumanMessage(content='Read the fixture')],
                                   'files': {skill_path: create_file_data(skill_text)}}, config)
            if result['messages'][-1].content != 'verified course-fixture':
                raise RuntimeError('Deep Agents tool loop did not produce expected result')
            results = {m.name: m for m in result['messages'] if isinstance(m, ToolMessage)}
            if any(m.status == 'error' for m in results.values()) or results['read_fixture'].content != 'course-fixture':
                raise RuntimeError('Deep Agents filesystem/skill tool failed')
            if 'read_fixture' in model.visible_tools[0] or 'read_fixture' not in model.visible_tools[-1]:
                raise RuntimeError('Official SkillsMiddleware did not progressively disclose its tool')
            if result['files']['/notes.md']['content'] != 'course-fixture':
                raise RuntimeError('Official StateBackend did not persist scratch output')
            # Exercise the upstream compaction and recoverable-history mechanism,
            # not merely the presence of a middleware name in graph configuration.
            summaries = []
            def summarize(messages):
                summaries.append(messages)
                return 'Earlier customer messages retained in the offloaded transcript.'
            summary_model = SyntheticModel(responses=[AIMessage(content='summary verified')])
            summary_backend = StateBackend()
            summary_agent = create_deep_agent(summary_model, backend=summary_backend,
                middleware=[SummarizationMiddleware(summary_model, backend=summary_backend,
                    trigger=('messages', 6), keep=('messages', 2), summarizer=summarize)],
                checkpointer=saver)
            history = []
            for index in range(6):
                history.extend([HumanMessage(content=f'original-order-{index}'), AIMessage(content='noted')])
            history.append(HumanMessage(content='Compare earlier orders'))
            summary_config = {'configurable': {'thread_id': 'runtime-summary'}, 'recursion_limit': 16}
            summary_result = summary_agent.invoke({'messages': history}, summary_config)
            # Private middleware channels are deliberately omitted from invoke's
            # public output but remain in the checkpoint's complete state.
            event = summary_agent.get_state(summary_config).values.get('_summarization_event')
            if not summaries or not event or not event.get('file_path'):
                raise RuntimeError('Deep Agents did not summarize and offload conversation history')
            transcript = summary_result['files'][event['file_path']]['content']
            if 'original-order-0' not in transcript or len(summary_result['messages']) < len(history):
                raise RuntimeError('Compaction lost the original transcript')
            if len(summary_model.seen_messages[-1]) >= len(history) or not any(
                    event['file_path'] in str(m.content) for m in summary_model.seen_messages[-1]):
                raise RuntimeError('Model did not receive compacted context with a recovery path')
        finally:
            conn.close()
        # Read persisted official graph channels using a fresh SQLite connection.
        conn = sqlite3.connect(str(Path(directory) / 'state.sqlite'), check_same_thread=False)
        try:
            saver = SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
            agent = create_deep_agent(SyntheticModel(), backend=StateBackend(), checkpointer=saver)
            state = agent.get_state(config)
            if state.values['messages'][-1].content != 'verified course-fixture':
                raise RuntimeError('Deep Agents SQLite checkpoint did not survive reopening')
            if state.values['files']['/notes.md']['content'] != 'course-fixture':
                raise RuntimeError('Deep Agents scratch filesystem did not survive reopening')
            summary_state = agent.get_state({'configurable': {'thread_id': 'runtime-summary'}})
            if not summary_state.values.get('_summarization_event'):
                raise RuntimeError('Summary state did not survive reopening')
        finally:
            conn.close()
        # Construct the provider without a request or account credentials.
        model = ChatOpenRouter(model='synthetic/offline', api_key='offline-placeholder', max_retries=0)
        if not model.bind_tools([read_fixture]):
            raise RuntimeError('Provider adapter could not bind a tool')
    return dict(toolLoop=True, sqliteReopened=True, openrouterAdapter=True,
                deepagentsFramework=True, filesystem=True, progressiveSkills=True,
                summaryOffload=True, rawHistoryPreserved=True, scratchCheckpoint=True,
                networkAccess=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--requirements', type=Path)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = dict(python=platform.python_version(), architecture=platform.machine())
    if args.requirements:
        report.update(verify_requirements(args.requirements))
    if args.smoke:
        report['agent'] = smoke_agent()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
