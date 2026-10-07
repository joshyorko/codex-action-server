"""Register ordinary actions using the source-owned deployment contract."""

from functools import wraps

from actions import ActionError, action as _runtime_action

from action_catalog_contract import (
    CAPABILITIES,
    PACKAGE_ACTION_NAMES,
    action_names_for_deployment,
    selected_package_names,
)


def action(*, package: str = "codex-action-server"):
    def decorate(function):
        name = function.__name__
        if name not in CAPABILITIES:
            raise ValueError(f"Action {name!r} is missing from the catalog contract")
        if (
            package not in PACKAGE_ACTION_NAMES
            or package not in CAPABILITIES[name].packages
        ):
            raise ValueError(f"Action {name!r} does not belong to package {package!r}")
        allowed = action_names_for_deployment()
        if package in selected_package_names() and name in allowed:
            return _runtime_action(
                is_consequential=CAPABILITIES[name].is_consequential
            )(function)

        @wraps(function)
        def denied(*_args, **_kwargs):
            raise ActionError(
                f"Action {name!r} is unavailable in the configured package/profile deployment"
            )

        return denied

    return decorate
