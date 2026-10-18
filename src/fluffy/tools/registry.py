"""Tool registry.

One declaration produces the model-facing function schema, argument checks,
the ``GET /api/tools`` manifest, and the mock-home handler.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from fluffy.config import HomeConfig


class ToolArgumentError(ValueError):
    """Arguments were malformed, unknown, or outside the home configuration."""


Handler = Callable[["Any", BaseModel, "Any"], "Any"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    action_class: Literal["comfort", "notify", "safety"]
    description: str
    model: type[BaseModel]
    handler: Handler | None = None


@dataclass(frozen=True)
class ParsedCall:
    """A model tool call after ``confidence`` and ``reason`` are removed."""

    tool: str
    args: dict[str, Any]
    confidence: float
    reason: str
    action_class: Literal["comfort", "notify", "safety"]


class Registry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def tool(
        self,
        *,
        name: str,
        action_class: Literal["comfort", "notify", "safety"],
        description: str,
    ) -> Callable[[type[BaseModel]], type[BaseModel]]:
        def decorate(model: type[BaseModel]) -> type[BaseModel]:
            self._specs[name] = ToolSpec(
                name=name,
                action_class=action_class,
                description=description,
                model=model,
            )
            return model

        return decorate

    def set_handler(self, name: str, handler: Handler) -> None:
        spec = self._specs[name]
        self._specs[name] = ToolSpec(
            name=spec.name,
            action_class=spec.action_class,
            description=spec.description,
            model=spec.model,
            handler=handler,
        )

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ToolArgumentError(f"unknown tool: {name}") from exc


registry = Registry()
tool = registry.tool


@dataclass(frozen=True)
class BoundTools:
    """Registry specialized to one validated home configuration."""

    home: HomeConfig

    def spec(self, name: str) -> ToolSpec:
        return registry.get(name)

    def action_class(self, name: str) -> Literal["comfort", "notify", "safety"]:
        return self.spec(name).action_class

    def parse_model_call(self, name: str, raw: str | dict[str, Any]) -> ParsedCall:
        """Parse a Perceptron tool call, including confidence and reason."""

        data = _object_from_raw(raw)
        if "confidence" not in data or "reason" not in data:
            raise ToolArgumentError("model tool calls require confidence and reason")
        confidence, reason = _model_fields(data)
        payload = {key: value for key, value in data.items() if key not in {"confidence", "reason"}}
        args = self.parse_plan_arguments(name, payload)
        return ParsedCall(
            tool=name,
            args=args,
            confidence=confidence,
            reason=reason,
            action_class=self.action_class(name),
        )

    def parse_plan_arguments(self, name: str, raw: str | dict[str, Any]) -> dict[str, Any]:
        """Validate arguments that are already part of a plan."""

        data = _object_from_raw(raw)
        stray = {"confidence", "reason"} & data.keys()
        if stray:
            joined = ", ".join(sorted(stray))
            raise ToolArgumentError(f"{joined} must be removed before the plan is built")
        spec = self.spec(name)
        try:
            model = spec.model.model_validate(data, context={"home": self.home})
        except ValidationError as exc:
            raise ToolArgumentError(_format_validation(exc)) from exc
        return model.model_dump(mode="json", exclude_none=True)

    def apply(self, state: Any, name: str, args: dict[str, Any], now: Any) -> Any:
        spec = self.spec(name)
        if spec.handler is None:
            raise ToolArgumentError(f"tool has no handler: {name}")
        try:
            model = spec.model.model_validate(args, context={"home": self.home})
        except ValidationError as exc:
            raise ToolArgumentError(_format_validation(exc)) from exc
        return spec.handler(state, model, now)

    def execution_schema(self, name: str) -> dict[str, Any]:
        return _execution_schema(self.spec(name).model, self.home)

    def model_schema(self, name: str) -> dict[str, Any]:
        schema = self.execution_schema(name)
        properties = dict(schema["properties"])
        properties["confidence"] = {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Removed before the plan is built.",
        }
        properties["reason"] = {
            "type": "string",
            "minLength": 1,
            "description": "Removed before the plan is built.",
        }
        required = list(schema.get("required", []))
        required.extend(["confidence", "reason"])
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
            "description": self.spec(name).description,
        }

    def manifest(self) -> dict[str, Any]:
        tools = []
        for spec in registry.specs():
            tools.append(
                {
                    "name": spec.name,
                    "action_class": spec.action_class,
                    "description": spec.description,
                    "parameters": self.execution_schema(spec.name),
                    "model_parameters": self.model_schema(spec.name),
                }
            )
        return {
            "schema_version": "1.0",
            "devices": self.home.devices_manifest(),
            "tools": tools,
        }


def configure(home: HomeConfig) -> BoundTools:
    """Bind the registry to a home. Importing tools registers every handler."""

    import fluffy.tools.home_state  # noqa: F401

    return BoundTools(home)


def _object_from_raw(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolArgumentError(f"malformed arguments: {exc.msg}") from exc
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        raise ToolArgumentError("arguments must be a JSON object")
    return parsed


def _model_fields(data: dict[str, Any]) -> tuple[float, str]:
    confidence = data.get("confidence")
    reason = data.get("reason")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ToolArgumentError("confidence must be a number from 0 to 1")
    if not 0 <= float(confidence) <= 1:
        raise ToolArgumentError("confidence must be a number from 0 to 1")
    if not isinstance(reason, str) or not reason.strip():
        raise ToolArgumentError("reason must be a non-empty string")
    return float(confidence), reason


def _format_validation(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error["loc"])
        parts.append(f"{loc}: {error['msg']}" if loc else error["msg"])
    return "; ".join(parts)


def _execution_schema(model: type[BaseModel], home: HomeConfig) -> dict[str, Any]:
    enum_fields: dict[str, str] = getattr(model, "enum_fields", {})
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in model.model_fields.items():
        annotation, optional = _unwrap(field.annotation)
        schema = _type_schema(annotation)
        kind = enum_fields.get(name)
        if kind is not None:
            schema = {"type": "string", "enum": home.enum_values(kind)}
        constraints = _constraints(field.metadata)
        schema.update(constraints)
        if field.description:
            schema["description"] = field.description
        if optional:
            description = schema.pop("description", None)
            schema = {"anyOf": [schema, {"type": "null"}]}
            if description:
                schema["description"] = description
        if not field.is_required():
            if field.default is not None:
                schema["default"] = field.default
        else:
            required.append(name)
        properties[name] = schema
    body: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        body["required"] = required
    return body


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    import types

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    is_union = isinstance(annotation, types.UnionType) or origin in {types.UnionType, __import__("typing").Union}
    if not is_union:
        return annotation, False
    remaining = [arg for arg in args if arg is not type(None)]
    optional = len(remaining) != len(args)
    if len(remaining) == 1:
        return remaining[0], optional
    return annotation, optional


def _type_schema(annotation: Any) -> dict[str, Any]:
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is str:
        return {"type": "string"}
    return {"type": "string"}


def _constraints(metadata: list[Any]) -> dict[str, Any]:
    import annotated_types

    found: dict[str, Any] = {}
    for item in metadata:
        if isinstance(item, annotated_types.Ge):
            found["minimum"] = item.ge
        elif isinstance(item, annotated_types.Gt):
            found["exclusiveMinimum"] = item.gt
        elif isinstance(item, annotated_types.Le):
            found["maximum"] = item.le
        elif isinstance(item, annotated_types.Lt):
            found["exclusiveMaximum"] = item.lt
        elif isinstance(item, annotated_types.MinLen):
            found["minLength"] = item.min_length
        elif isinstance(item, annotated_types.MaxLen):
            found["maxLength"] = item.max_length
    return found

