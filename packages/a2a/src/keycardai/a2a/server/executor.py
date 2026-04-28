"""Agent executor abstraction for framework-agnostic service implementation.

This module provides the executor pattern that separates the generic agent service
server from specific framework implementations (CrewAI, LangChain, AutoGen, etc.).

The AgentExecutor protocol defines a simple interface:
- execute(task, inputs) -> result

This allows the server to be framework-agnostic while supporting multiple
agent frameworks through adapters.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentExecutor(Protocol):
    """Protocol for agent executors.

    An executor is responsible for taking a task and inputs, running the
    agent/crew/chain, and returning the result. This abstraction allows
    the server to support multiple agent frameworks.

    Implementations should be thread-safe if used in async contexts.

    Example:
        >>> class MyCustomExecutor:
        ...     def execute(self, task: dict[str, Any], inputs: dict[str, Any] | None) -> Any:
        ...         # Run your agent framework
        ...         result = my_agent.run(task, inputs)
        ...         return result
        >>>
        >>> # Use in server config
        >>> config = AgentServiceConfig(
        ...     service_name="My Service",
        ...     agent_executor=MyCustomExecutor(),
        ...     # ... other config
        ... )
    """

    def execute(
        self,
        task: dict[str, Any] | str,
        inputs: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an agent task.

        Args:
            task: Task description or parameters. Can be:
                - str: Simple task description
                - dict: Structured task with parameters
            inputs: Optional additional inputs/context for execution

        Returns:
            Result from agent execution (any JSON-serializable type)

        Raises:
            Exception: If execution fails

        Note:
            Implementations should handle both synchronous and asynchronous
            execution as needed for their framework. The server will call
            this method synchronously within the async endpoint.
        """
        ...


class SimpleExecutor:
    """Simple executor that returns the task as-is (for testing).

    This executor is useful for testing the server without a full agent
    framework. It simply echoes back the task and inputs.

    Example:
        >>> executor = SimpleExecutor()
        >>> result = executor.execute("Hello", {"name": "World"})
        >>> print(result)
        {'task': 'Hello', 'inputs': {'name': 'World'}, 'message': 'Executed by SimpleExecutor'}
    """

    def execute(
        self,
        task: dict[str, Any] | str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task by echoing it back.

        Args:
            task: Task description or parameters
            inputs: Optional inputs

        Returns:
            Dictionary with task, inputs, and confirmation message
        """
        return {
            "task": task,
            "inputs": inputs,
            "message": "Executed by SimpleExecutor",
        }


class LambdaExecutor:
    """Executor that wraps a simple callable/lambda.

    This executor is useful for quick prototyping or simple agent logic
    without needing a full framework.

    Args:
        func: Callable that takes (task, inputs) and returns a result

    Example:
        >>> def my_agent(task, inputs):
        ...     return f"Processed: {task} with {inputs}"
        >>>
        >>> executor = LambdaExecutor(my_agent)
        >>> result = executor.execute("analyze", {"data": [1, 2, 3]})
    """

    def __init__(self, func: Any):
        """Initialize lambda executor.

        Args:
            func: Callable(task, inputs) -> result
        """
        self.func = func

    def execute(
        self,
        task: dict[str, Any] | str,
        inputs: dict[str, Any] | None = None,
    ) -> Any:
        """Execute task using the wrapped callable.

        Args:
            task: Task description or parameters
            inputs: Optional inputs

        Returns:
            Result from callable
        """
        return self.func(task, inputs)
