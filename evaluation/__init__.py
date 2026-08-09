import sys
import types

# ragas (0.4.3, latest as of writing) unconditionally imports
# langchain_community.chat_models.vertexai at module load time, purely to
# put ChatVertexAI in an isinstance() check list -- we only ever pass
# Azure OpenAI-backed LLM wrappers (see judge.py), so that check never
# matches regardless. langchain-community has since removed that submodule
# entirely (moved to the standalone langchain-google-vertexai package,
# itself a heavy Google Cloud SDK dependency tree we don't otherwise need),
# so the bare import fails before ragas is even usable.
#
# Registering a stub module here -- before anything imports ragas -- avoids
# pulling in an unrelated multi-package dependency tree just to satisfy an
# import of a class we can never actually trigger.
if "langchain_community.chat_models.vertexai" not in sys.modules:
    _stub = types.ModuleType("langchain_community.chat_models.vertexai")
    _stub.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules["langchain_community.chat_models.vertexai"] = _stub
