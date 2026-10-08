"""Existing, UID-pinned Kubernetes workers; no provisioning or daemon lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
import shlex
import subprocess


class KubernetesError(ValueError):
    """Stable errors without kubeconfig contents or raw transport output."""


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}", value
    ):
        raise KubernetesError("invalid_kubernetes_identity")
    return value


def _path(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or any(c.isspace() or ord(c) < 32 for c in value)
        or ".." in PurePosixPath(value).parts
    ):
        raise KubernetesError("invalid_kubernetes_configuration")
    return value


@dataclass(frozen=True)
class KubernetesWorker:
    kubeconfig: str
    context: str
    namespace: str
    workspace_id: str
    workspace_uid: str
    pod: str
    pod_uid: str
    codex_bin: str
    codex_home: str = "/home/vscode/.codex"

    @classmethod
    def resolve(cls, row, codex_bin):
        try:
            options = row["provider"]["options"]
            config = _path(options["KUBERNETES_CONFIG"]["value"])
            context = _name(options["KUBERNETES_CONTEXT"]["value"])
            namespace = _name(options["KUBERNETES_NAMESPACE"]["value"])
            workspace = _name(row["id"])
            uid = _name(row["uid"])
            binary = _path(codex_bin)
        except (KeyError, TypeError, AttributeError):
            raise KubernetesError("invalid_kubernetes_configuration") from None
        prefix = [
            "kubectl",
            "--kubeconfig",
            config,
            "--context",
            context,
            "--namespace",
            namespace,
        ]
        items = cls._query(
            prefix
            + ["get", "pods", "-l", "devsy.sh/workspace-uid=" + uid, "-o", "json"]
        ).get("items")
        if not isinstance(items, list) or len(items) != 1:
            raise KubernetesError("workspace_pod_missing_or_ambiguous")
        pod = items[0]
        cls._validate(pod, namespace, uid)
        return cls(
            config,
            context,
            namespace,
            workspace,
            uid,
            _name(pod["metadata"]["name"]),
            _name(pod["metadata"]["uid"]),
            binary,
        )

    @staticmethod
    def _query(args):
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=10, check=True
            )
            if len(result.stdout) > 2 * 1024 * 1024:
                raise KubernetesError("kubernetes_probe_output_too_large")
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise KubernetesError("invalid_kubernetes_response")
            return value
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            if isinstance(error, KubernetesError):
                raise
            raise KubernetesError("kubernetes_probe_failed") from None

    @staticmethod
    def _validate(pod, namespace, uid):
        try:
            if (
                pod["metadata"]["namespace"] != namespace
                or pod["metadata"]["labels"].get("devsy.sh/workspace-uid") != uid
            ):
                raise KubernetesError("workspace_identity_changed")
            if pod["status"]["phase"] != "Running":
                raise KubernetesError("workspace_not_running")
            containers = [
                item
                for item in pod["status"].get("containerStatuses", [])
                if item["name"] == "devsy"
            ]
            if (
                len(containers) != 1
                or not containers[0].get("ready")
                or "running" not in containers[0].get("state", {})
            ):
                raise KubernetesError("workspace_not_ready")
        except (KeyError, TypeError, AttributeError):
            raise KubernetesError("invalid_kubernetes_response") from None

    def prefix(self):
        return [
            "kubectl",
            "--kubeconfig",
            self.kubeconfig,
            "--context",
            self.context,
            "--namespace",
            self.namespace,
        ]

    def command(self, args):
        pod = self._query(self.prefix() + ["get", "pod", self.pod, "-o", "json"])
        self._validate(pod, self.namespace, self.workspace_uid)
        if pod["metadata"].get("uid") != self.pod_uid:
            raise KubernetesError("workspace_pod_identity_changed")
        native = shlex.join(
            ["env", "CODEX_HOME=" + self.codex_home, self.codex_bin, *args]
        )
        command = (
            'test "${DEVSY_WORKSPACE_UID:-}" = '
            + shlex.quote(self.workspace_uid)
            + ' && test "${DEVSY_WORKSPACE_ID:-}" = '
            + shlex.quote(self.workspace_id)
            + " && exec "
            + shlex.join(["su", "-s", "/bin/sh", "-c", native, "vscode"])
        )
        return self.prefix() + [
            "exec",
            "--stdin",
            self.pod,
            "--container",
            "devsy",
            "--",
            "/bin/sh",
            "-c",
            command,
        ]
