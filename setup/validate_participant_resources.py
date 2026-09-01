#!/usr/bin/env python3
"""Read-only validation of the exact resources assigned to a workshop team."""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import AzureError, HttpResponseError
from azure.identity import AzureCliCredential
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient
from dotenv import dotenv_values


SEARCH_SCOPE = "https://search.azure.com/.default"
T = TypeVar("T")

CORE_REQUIRED_SETTINGS = (
    "TENANT_ID",
    "WORKSHOP_RESOURCE_NAMESPACE",
    "FOUNDRY_PROJECT_ENDPOINT",
    "FOUNDRY_MODEL",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_CONNECTION_NAME",
    "AZURE_SEARCH_INDEX",
)

FOUNDRY_IQ_SETTINGS = (
    "AZURE_SEARCH_RO_INDEX",
    "AZURE_SEARCH_CREW_INDEX",
    "FOUNDRY_IQ_KNOWLEDGE_BASE",
    "FOUNDRY_IQ_MCP_CONNECTION_NAME",
    "FOUNDRY_IQ_API_VERSION",
    "FOUNDRY_IQ_PROCEDURES_INDEX",
    "FOUNDRY_IQ_RAAMOPDRACHTEN_INDEX",
    "FOUNDRY_IQ_CREW_INDEX",
    "FOUNDRY_IQ_PROCEDURES_SOURCE",
    "FOUNDRY_IQ_RAAMOPDRACHTEN_SOURCE",
    "FOUNDRY_IQ_CREW_SOURCE",
)

STORAGE_SETTINGS = (
    "AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_CREW_CONTAINER",
    "AZURE_STORAGE_CREW_BLOB",
    "AZURE_STORAGE_RO_BLOB",
)

PARTICIPANT_AGENT_NAMES = (
    ("DRAAD_RETRIEVER_AGENT", "draad-procedure-retriever"),
    ("DRAAD_MATCHER_AGENT", "draad-dispatch-matcher"),
    ("DRAAD_REVIEWER_AGENT", "draad-dispatch-reviewer"),
    ("DRAAD_QA_AGENT", "draad-qa-assistant"),
)


class ResourceValidationError(RuntimeError):
    """A participant resource is missing, empty, inaccessible, or mismatched."""


def _validate_optional_group(
    values: dict[str, str],
    names: tuple[str, ...],
    label: str,
    *,
    required: bool,
) -> bool:
    configured = [name for name in names if values.get(name)]
    if not configured:
        if required:
            raise ResourceValidationError(
                f"{label} is required but its settings are missing: "
                + ", ".join(names)
            )
        return False
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise ResourceValidationError(
            f"{label} configuration is partial; missing: " + ", ".join(missing)
        )
    return True


def load_settings(
    path: Path,
    *,
    require_foundry_iq: bool = False,
    require_storage: bool = False,
) -> dict[str, str]:
    values = {
        key: str(value).strip()
        for key, value in dotenv_values(path).items()
        if value is not None
    }
    missing = [name for name in CORE_REQUIRED_SETTINGS if not values.get(name)]
    if missing:
        raise ResourceValidationError(
            "missing required environment setting(s): " + ", ".join(missing)
        )
    _validate_optional_group(
        values,
        FOUNDRY_IQ_SETTINGS,
        "Foundry IQ",
        required=require_foundry_iq,
    )
    _validate_optional_group(
        values,
        STORAGE_SETTINGS,
        "Blob Storage",
        required=require_storage,
    )
    return values


def foundry_iq_configured(settings: dict[str, str]) -> bool:
    return all(settings.get(name) for name in FOUNDRY_IQ_SETTINGS)


def storage_configured(settings: dict[str, str]) -> bool:
    return all(settings.get(name) for name in STORAGE_SETTINGS)


def participant_agent_names(settings: dict[str, str]) -> tuple[str, ...]:
    """Resolve the four exact runtime names without importing reference prompts."""
    raw_namespace = settings["WORKSHOP_RESOURCE_NAMESPACE"]
    namespace = re.sub(
        r"[^a-z0-9-]+", "-", raw_namespace.strip().lower()
    ).strip("-")
    if raw_namespace.strip() and not namespace:
        raise ResourceValidationError(
            "WORKSHOP_RESOURCE_NAMESPACE must contain a letter or digit"
        )
    if len(namespace) > 24:
        digest = sha256(raw_namespace.strip().encode("utf-8")).hexdigest()[:6]
        namespace = f"{namespace[:17].rstrip('-')}-{digest}"
    names = tuple(
        settings.get(environment_name)
        or (f"{base_name}-{namespace}" if namespace else base_name)
        for environment_name, base_name in PARTICIPANT_AGENT_NAMES
    )
    if len(set(names)) != len(names):
        raise ResourceValidationError(
            "participant runtime agent names must be four distinct values"
        )
    return names


def with_project_retry(
    label: str,
    operation: Callable[[], T],
    *,
    attempts: int = 6,
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except HttpResponseError as exc:
            transient = exc.status_code == 404 and "project not found" in str(exc).lower()
            if not transient or attempt == attempts:
                raise ResourceValidationError(f"{label}: {exc}") from exc
            time.sleep(min(5 * (2 ** (attempt - 1)), 30))
    raise AssertionError("retry loop exhausted without returning or raising")


def resolve_project_connection(
    project: AIProjectClient,
    name: str,
    label: str,
    *,
    attempts: int = 6,
):
    """Resolve one named connection while a fresh project data plane propagates."""
    for attempt in range(1, attempts + 1):
        try:
            matches = [
                connection
                for connection in project.connections.list()
                if getattr(connection, "name", None) == name
            ]
        except HttpResponseError as exc:
            transient = exc.status_code == 404 and "project not found" in str(exc).lower()
            if not transient or attempt == attempts:
                raise ResourceValidationError(f"{label}: {exc}") from exc
            matches = []

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ResourceValidationError(
                f"{label}: expected one connection named {name!r}; found {len(matches)}"
            )
        if attempt == attempts:
            raise ResourceValidationError(
                f"{label}: connection {name!r} was not visible after {attempts} attempts"
            )
        time.sleep(min(5 * (2 ** (attempt - 1)), 30))

    raise AssertionError("connection retry loop exhausted without returning or raising")


def validate_project_resources(
    settings: dict[str, str],
    credential: AzureCliCredential,
    *,
    require_empty_agent_names: bool = False,
) -> None:
    with AIProjectClient(
        endpoint=settings["FOUNDRY_PROJECT_ENDPOINT"],
        credential=credential,
    ) as project:
        deployment = with_project_retry(
            "Foundry model deployment",
            lambda: project.deployments.get(settings["FOUNDRY_MODEL"]),
        )
        if getattr(deployment, "name", None) != settings["FOUNDRY_MODEL"]:
            raise ResourceValidationError("Foundry model deployment name does not match")
        print(f"RESOURCE PASS  Foundry model deployment: {settings['FOUNDRY_MODEL']}")

        connections = [
            ("Azure AI Search project connection", settings["AZURE_SEARCH_CONNECTION_NAME"])
        ]
        if foundry_iq_configured(settings):
            connections.append(
                (
                    "Foundry IQ MCP project connection",
                    settings["FOUNDRY_IQ_MCP_CONNECTION_NAME"],
                )
            )
        for label, name in connections:
            resolve_project_connection(project, name, label)
            print(f"RESOURCE PASS  {label}: {name}")

        if require_empty_agent_names:
            validate_participant_agent_names_empty(project, settings)


def validate_participant_agent_names_empty(
    project: AIProjectClient,
    settings: dict[str, str],
    *,
    attempts: int = 6,
) -> None:
    """Fail the handoff gate when a completed agent already occupies a lab name."""
    if attempts < 2:
        raise ValueError("empty-agent validation requires at least two observations")
    expected_names = set(participant_agent_names(settings))
    conflicts: list[str] = []
    consecutive_empty_observations = 0
    for attempt in range(1, attempts + 1):
        agents = with_project_retry(
            "Foundry participant agent inventory",
            lambda: list(project.agents.list(limit=100)),
            attempts=attempts,
        )
        actual_names = {
            str(agent.name)
            for agent in agents
            if getattr(agent, "name", None) is not None
        }
        conflicts = sorted(expected_names & actual_names)
        if not conflicts:
            consecutive_empty_observations += 1
            if consecutive_empty_observations >= 2:
                print(
                    "RESOURCE PASS  Participant agent names are empty before handoff "
                    "(2 consecutive observations)."
                )
                return
        else:
            consecutive_empty_observations = 0
        if attempt < attempts:
            delay = 2 if not conflicts else min(5 * (2 ** (attempt - 1)), 30)
            time.sleep(delay)

    if conflicts:
        raise ResourceValidationError(
            "participant agent names already contain deployed versions: "
            + ", ".join(conflicts)
            + ". Remove reference versions before handoff."
        )
    raise ResourceValidationError(
        "participant agent names did not remain empty for two consecutive observations"
    )


def validate_search_indexes(
    settings: dict[str, str],
    credential: AzureCliCredential,
) -> None:
    index_names = [settings["AZURE_SEARCH_INDEX"]]
    if foundry_iq_configured(settings):
        index_names.extend(
            (
                settings["FOUNDRY_IQ_PROCEDURES_INDEX"],
                settings["FOUNDRY_IQ_RAAMOPDRACHTEN_INDEX"],
                settings["FOUNDRY_IQ_CREW_INDEX"],
            )
        )
    indexes = dict.fromkeys(index_names)
    for index_name in indexes:
        with SearchClient(
            endpoint=settings["AZURE_SEARCH_ENDPOINT"],
            index_name=index_name,
            credential=credential,
        ) as client:
            count = client.get_document_count()
        if count < 1:
            raise ResourceValidationError(
                f"Azure AI Search index {index_name!r} exists but contains no documents"
            )
        print(f"RESOURCE PASS  Search index: {index_name} ({count} documents)")


def _get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise ResourceValidationError(
            f"Foundry IQ resource request returned HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ResourceValidationError(
            f"Foundry IQ resource request failed: {exc.reason}"
        ) from exc


def _search_resource_url(
    settings: dict[str, str],
    resource_type: str,
    name: str,
) -> str:
    return (
        settings["AZURE_SEARCH_ENDPOINT"].rstrip("/")
        + f"/{resource_type}/"
        + urllib.parse.quote(name, safe="")
        + "?api-version="
        + urllib.parse.quote(settings["FOUNDRY_IQ_API_VERSION"], safe="")
    )


def validate_foundry_iq(
    settings: dict[str, str],
    credential: AzureCliCredential,
) -> None:
    name = settings["FOUNDRY_IQ_KNOWLEDGE_BASE"]
    token = credential.get_token(SEARCH_SCOPE).token
    definition = _get_json(
        _search_resource_url(settings, "knowledgebases", name),
        token,
    )
    source_index_pairs = (
        (
            settings["FOUNDRY_IQ_PROCEDURES_SOURCE"],
            settings["FOUNDRY_IQ_PROCEDURES_INDEX"],
        ),
        (
            settings["FOUNDRY_IQ_RAAMOPDRACHTEN_SOURCE"],
            settings["FOUNDRY_IQ_RAAMOPDRACHTEN_INDEX"],
        ),
        (
            settings["FOUNDRY_IQ_CREW_SOURCE"],
            settings["FOUNDRY_IQ_CREW_INDEX"],
        ),
    )
    actual_knowledge_base_name = definition.get("name")
    if actual_knowledge_base_name != name:
        raise ResourceValidationError(
            f"Foundry IQ knowledge base returned name {actual_knowledge_base_name!r}; "
            f"expected {name!r}"
        )

    raw_sources = definition.get("knowledgeSources")
    if not isinstance(raw_sources, list) or not all(
        isinstance(item, dict) and isinstance(item.get("name"), str)
        for item in raw_sources
    ):
        raise ResourceValidationError(
            "Foundry IQ knowledge base returned an invalid knowledgeSources contract"
        )
    actual_source_names = [item["name"] for item in raw_sources]
    expected_source_names = {source for source, _ in source_index_pairs}
    actual_source_name_set = set(actual_source_names)
    if (
        len(actual_source_names) != len(actual_source_name_set)
        or actual_source_name_set != expected_source_names
    ):
        missing = sorted(expected_source_names - actual_source_name_set)
        unexpected = sorted(actual_source_name_set - expected_source_names)
        raise ResourceValidationError(
            "Foundry IQ knowledge base source membership mismatch: "
            f"missing={missing}, unexpected={unexpected}, "
            f"entries={len(actual_source_names)}"
        )

    for source_name, index_name in source_index_pairs:
        source = _get_json(
            _search_resource_url(settings, "knowledgesources", source_name),
            token,
        )
        actual_source_name = source.get("name")
        actual_index_name = (source.get("searchIndexParameters") or {}).get(
            "searchIndexName"
        )
        if actual_source_name != source_name or actual_index_name != index_name:
            raise ResourceValidationError(
                f"Foundry IQ knowledge source {source_name!r} targets "
                f"{actual_index_name!r}; expected {index_name!r}"
            )
        print(
            f"RESOURCE PASS  Foundry IQ knowledge source: "
            f"{source_name} -> {index_name}"
        )
    print(f"RESOURCE PASS  Foundry IQ knowledge base: {name} (3 sources)")


def validate_storage(
    settings: dict[str, str],
    credential: AzureCliCredential,
) -> None:
    service = BlobServiceClient(
        account_url=settings["AZURE_STORAGE_ACCOUNT_URL"],
        credential=credential,
    )
    container_name = settings["AZURE_STORAGE_CREW_CONTAINER"]
    container = service.get_container_client(container_name)
    try:
        container.get_container_properties()
        for blob_name in (
            settings["AZURE_STORAGE_CREW_BLOB"],
            settings["AZURE_STORAGE_RO_BLOB"],
        ):
            container.get_blob_client(blob_name).get_blob_properties()
            print(f"RESOURCE PASS  Blob: {container_name}/{blob_name}")
    finally:
        container.close()
        service.close()


def validate(
    environment_file: Path,
    *,
    require_foundry_iq: bool = False,
    require_storage: bool = False,
    require_empty_agent_names: bool = False,
) -> None:
    settings = load_settings(
        environment_file,
        require_foundry_iq=require_foundry_iq,
        require_storage=require_storage,
    )
    credential = AzureCliCredential(tenant_id=settings["TENANT_ID"])
    try:
        validate_project_resources(
            settings,
            credential,
            require_empty_agent_names=require_empty_agent_names,
        )
        validate_search_indexes(settings, credential)
        if foundry_iq_configured(settings):
            validate_foundry_iq(settings, credential)
        else:
            print("RESOURCE WARN  Foundry IQ is not configured; direct Search fallback remains available.")
        if storage_configured(settings):
            validate_storage(settings, credential)
        else:
            print("RESOURCE WARN  Blob Storage ingestion extension is not configured.")
    finally:
        credential.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-file", type=Path, required=True)
    parser.add_argument("--require-foundry-iq", action="store_true")
    parser.add_argument("--require-storage", action="store_true")
    parser.add_argument(
        "--require-empty-agent-names",
        action="store_true",
        help="Fail if any participant runtime agent name already exists.",
    )
    args = parser.parse_args()
    try:
        validate(
            args.environment_file.resolve(),
            require_foundry_iq=args.require_foundry_iq,
            require_storage=args.require_storage,
            require_empty_agent_names=args.require_empty_agent_names,
        )
    except (ResourceValidationError, AzureError, OSError) as exc:
        print(f"RESOURCE FAIL  {exc}")
        return 1
    print("RESOURCE PASS  Exact participant resource validation completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
