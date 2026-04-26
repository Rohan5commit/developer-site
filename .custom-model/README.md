# Custom model runtime

This directory contains the minimal local control/runtime files for the custom model stack.

## Components

- `custom_model.py` — local CLI launcher and orchestration path.
- `qwen_inference_gateway_primary.py` — Modal GPU inference runtime using the custom model weights plus a Mythos-style recurrent reasoning wrapper.
- `qwen_vm_bridge.py` — Modal quota ledger / hard-cap bridge.
- `qwen_web_search.py` — Modal CPU search fallback code path.
- `vm_web_search_server.py` — VM-side parallel web search server used over SSH tunnel.
- `modal_control.py` — shared Modal profile/function caller.
- `run` — thin launcher wrapper.

## Current architecture

- Base model weights remain the custom model.
- Safety/embedding path remains the existing OBLITERATUS + Heretic flow.
- Reasoning path uses a Mythos-style runtime:
  - Prelude
  - recurrent refinement loops
  - Coda final answer pass
- Live web search runs on the Alibaba VM, not Modal CPU.

## VM-side state

- Web search service runs on the VM as `custom-model-websearch.service`.
- OpenMythos reference repo is cloned on the VM at `/opt/openmythos-reference`.
- Current verified reference commit: `227dbb1`.

## Deployed app names

- `qwen-inference-gateway-mythos`
- `qwen-vm-bridge`

## Local-only files intentionally not committed

- `modal.toml` — contains Modal tokens
- `project.env` — machine-specific runtime config
- `session.json` — local chat/session state
