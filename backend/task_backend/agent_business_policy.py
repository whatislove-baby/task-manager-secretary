"""Application policy via upstream memory and summarization mechanisms.

No extra model loop, automatic skill publication or business-fact inference.
The immutable app document is always loaded, unlike opt-in SKILL.md workflows.
"""
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.summarization import DEEPAGENTS_DEFAULT_SUMMARY_PROMPT


BUSINESS_POLICY_PATH = '/skills/builtin/AGENTS.md'
POLICY_MEMORY_PROMPT = """<application_business_policy>
{agent_memory}
</application_business_policy>
以上是应用只读业务规范，不是客户邮件、临时事实或自动批准的用户技能。
本规范不可用 edit_file 修改；用户经验继续经技能库提议、测试和独立批准。
"""

SUMMARY_PRESERVATION = """<business_context_preservation>
Keep facts separated by mailbox account, exact sender, and business object.
For each important quantity, price basis, currency, tax, drawing revision and
requested date, preserve its original mail/file ID, scope and latest explicit
correction. Distinguish customer requests from supplier commitments.
Preserve what is already established by the relevant mail, attachment or user;
do not turn settled facts back into open questions. Keep genuinely unresolved
questions separately. A fact from a different order cannot prove this order's
fact unless the current order explicitly adopts that particular field.
Preserve current file/draft IDs, revision and actual lifecycle/attachment state,
including failed operations and remaining mismatches. A statement of intent is
not a completed operation. Do not repeat superseded values as current facts or
claim a file is archived/attached based only on an assistant's narrative.
When conflicting evidence is unresolved, retain both source IDs and the conflict;
do not resolve it by invention. This summary is an index; original sources remain
the authority for later critical decisions.
</business_context_preservation>"""


def business_policy_middleware(backend: BackendProtocol) -> MemoryMiddleware:
    """Use the official always-loaded policy hook with an existing virtual backend.

    make_backend() returns an actual CompositeBackend, not a factory. Retaining
    that object keeps reads on the SQLite-owning event loop and paths read-only.
    The default MemoryMiddleware prompt encourages automatic writes; its public
    template option lets us retain the application's explicit review boundary.
    """
    return MemoryMiddleware(backend=backend, sources=[BUSINESS_POLICY_PATH],
                            system_prompt=POLICY_MEMORY_PROMPT)


def business_summary_prompt() -> str:
    """Add business preservation to upstream's stable messages marker.

    Preserve upstream sections/media instructions and literal-brace escaping.
    Do not format the template here: LangChain supplies `messages` later.
    """
    marker = '\n<messages>\n'
    if marker not in DEEPAGENTS_DEFAULT_SUMMARY_PROMPT:
        raise ValueError('Upstream summary prompt no longer exposes its messages marker')
    return DEEPAGENTS_DEFAULT_SUMMARY_PROMPT.replace(
        marker, '\n' + SUMMARY_PRESERVATION + '\n' + marker, 1)
