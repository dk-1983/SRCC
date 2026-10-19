[**English**](CONTRIBUTING.md) · [**Русский**](CONTRIBUTING_RU.md)

# Contributing

SRCC includes a decision core, HA observation, and sequential equipment control. Keep hardware drivers and transports outside SRCC: use HA entities and services.

- Describe the problem, expected behavior, and evidence used to validate a change.
- Distinguish implemented requirements from proposals and provisional settings.
- Changes to guards, power handling, transitions, or recovery need meaningful regression scenarios and updated documentation.
- Keep related changes in focused commits. Do not introduce equipment-specific constants without a configuration model.
- Maintain English documentation as the default and Russian pages as the second language, with reciprocal language links.

README and docs contain architecture, limitations, requirements, instructions, and reproducible validation results. Keep personal notes, conversations, raw diagnostics, screenshots, and draft work in ignored `.local/`. Never commit credentials, internal addresses, or real installation exports. Use fictional entity IDs in examples.

Run the checks described in [Development](docs/DEVELOPMENT.md). For documentation-only changes, check links, language navigation, and technical accuracy. Record only checks actually performed; distinguish software simulation from field evidence.
