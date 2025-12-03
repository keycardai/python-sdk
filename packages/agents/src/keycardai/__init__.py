# Namespace package - allows keycardai.mcp and keycardai.agents to coexist
__path__ = __import__('pkgutil').extend_path(__path__, __name__)
