[**English**](DEVELOPMENT.md) · [**Русский**](DEVELOPMENT_RU.md)

# Development and reproducibility

Current release: 0.8.0. Software baseline: **95 standalone + 106 HA tests = 201 passed**, with hassfest reporting one integration and zero invalid. See [test catalog](TEST_CATALOG.md), [validation](VALIDATION_PLAN.md), and [architecture](ARCHITECTURE.md).

The HA test environment used HA 2026.9.2 and Python 3.14.5 inside a local hassfest image. The standalone tests used local Python. The `latest` Docker tag is mutable; check the actual local image before comparing environments. The historical tested image ID was `sha256:d13ffcc8995c1217804fe355ed078a81d3028b7002355bc5d89d19d9075d63c7`, not a guaranteed registry digest.

## Checks from the repository root

```powershell
python -m unittest discover -s tests -v
New-Item -ItemType Directory -Force .local | Out-Null
docker run --rm --network none --mount "type=bind,source=$($PWD.Path),target=/repo" -e PYTHONPATH=/repo:/usr/src/homeassistant --workdir /repo --entrypoint python ghcr.io/home-assistant/hassfest:latest -m unittest discover -s tests/ha -v
docker run --rm --network none --mount "type=bind,source=$($PWD.Path),target=/github/workspace,readonly" ghcr.io/home-assistant/hassfest:latest
```

Prepare the image before running offline checks. HA tests use real states, services, events, registries, and platform loading, but simulate equipment and selected transport responses. They do not contact the production HA installation. Passing tests do not prove a physical driver's readback semantics.

Policy changes need boundary and sequence tests. Execution changes need service-order, readback, missing-data, failure, and disable-during-action tests. Lifecycle changes need reload/unload coverage. UI/manifest/translations need hassfest and platform loading. Notification changes must remain isolated from cooling and must not leak raw source data.

Use simulated time for long scenarios. Many service-order tests set seasonal waits to zero; they do not replace real timing acceptance. Preserve the distinction between software PASS, visual observation, a state photograph, and a fully timestamped equipment trace.

## Documentation and release packaging

English pages use the standard `.md` name; Russian counterparts use `_RU.md`. Each has reciprocal language links. Russian pages retain detailed historical records; current behavior is defined by the newest version sections. Keep public content technical and reproducible; raw logs and scratch work belong in ignored `.local/`.

An installation ZIP contains only tracked files under `custom_components/fourvrs_server_room`, preserving that path. It must exclude caches, logs, local settings, credentials, and working notes. GitHub source archives include the public repository and bilingual documentation. Release version must match both manifest.json and const.py; record test results and field limitations in release notes.
