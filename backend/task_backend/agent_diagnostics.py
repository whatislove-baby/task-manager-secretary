"""Safe recovery messages for persisted desktop Agent runs."""

MESSAGES = {
    'connection': '模型连接中断或超时，已完成的文件和步骤保留。点击继续可恢复本轮任务。',
    'rate_limit': '模型服务暂时限制请求频率，已有进度保留。稍后可点击继续。',
    'authentication': '模型服务认证或访问授权失败，已有进度保留。请在模型设置中检查服务地址、密钥与权限。',
    'configuration': '请检查模型设置中的服务地址、模型和密钥。',
    'execution_limit': '本轮工具调用较多，已暂停继续执行。可以重试，或拆分当前任务。',
    'model_response': '模型回复无法完整读取，已有进度保留。请继续本轮或拆分任务。',
    'unknown': '助手执行失败，已有文件和草稿保留。请检查网络、模型工具能力及配置后重试。',
}

STATUS_CODES = {
    401: 'authentication',
    403: 'authentication',
    408: 'connection',
    429: 'rate_limit',
    504: 'connection',
    524: 'connection',
}


class AgentFailure(ValueError):
    """An Agent host validation failure with a fixed user-facing message."""

    def __init__(self, code, message=None):
        self.code = code
        self.message = message if message is not None else MESSAGES[code]
        super().__init__(self.message)


class EmptyModelResponse(AgentFailure):
    def __init__(self):
        super().__init__('model_response', '模型回复为空，请继续本轮任务。')


def diagnose_run_error(error):
    if isinstance(error, AgentFailure):
        return error

    # Keep framework/provider dependencies out of the desktop RPC startup path.
    try:
        from httpx import HTTPStatusError, NetworkError, ProxyError, RemoteProtocolError, TimeoutException
        from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
        from langgraph.errors import GraphRecursionError
        from openrouter.errors import NoResponseError, OpenRouterError, ResponseValidationError
        from .agent_openrouter import ReasoningMetadataConflict
    except ImportError:
        return AgentFailure('unknown')

    status = None
    if isinstance(error, HTTPStatusError):
        status = error.response.status_code
    elif isinstance(error, OpenRouterError):
        status = error.status_code
    if status in STATUS_CODES:
        return AgentFailure(STATUS_CODES[status])
    if isinstance(error, (ConnectionError, TimeoutError, NetworkError, ProxyError,
                          RemoteProtocolError, TimeoutException, NoResponseError)):
        return AgentFailure('connection')
    if isinstance(error, (GraphRecursionError, ModelCallLimitExceededError)):
        return AgentFailure('execution_limit')
    if isinstance(error, (ReasoningMetadataConflict, ResponseValidationError)):
        return AgentFailure('model_response')
    return AgentFailure('unknown')
