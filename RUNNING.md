# Running the pipeline (branch `anans-approach`)

How to run anan's variant (`variants/anan`) on our hardware, starting from machines with nothing installed. Every command says which machine it runs on. The workflow itself is drawn in [docs/diagrams/anan-workflow.svg](docs/diagrams/anan-workflow.svg).

| Name in this guide | Hardware / OS | Runs |
|---|---|---|
| **PC1**, **PC2** | GPU PC, 16 GB VRAM, **Ubuntu** (24.04 assumed) | `llama-server` with gpt-oss-20b |
| **ORCH** | CPU-only, **Windows** 10/11 | orchestrator (`python -m app.run`) + founder UI (Streamlit) |
| **SINGLE** | one GPU PC (Ubuntu) | everything (single-PC mode, section 10) |

**WSL:** not needed. The GPU PCs are native Linux. ORCH runs the Python app natively on Windows. WSL2 only comes in if you choose Docker on a Windows machine (Docker Desktop uses WSL2); this guide does not need that.

Replace these placeholders everywhere: `192.168.1.10` = PC1's IP, `192.168.1.11` = PC2's IP, `192.168.1.0/24` = your LAN subnet.

---

## 1. Prerequisites

### PC1 and PC2 (Ubuntu, GPU)

NVIDIA driver 570 or newer (needed for CUDA 12.8; also covers RTX 50-series):

```bash
# PC1 and PC2
sudo apt update
sudo apt install -y git build-essential cmake curl libcurl4-openssl-dev python3-venv python3-pip ufw
sudo ubuntu-drivers install          # or: sudo apt install -y nvidia-driver-570
sudo reboot
```

After the reboot:

```bash
# PC1 and PC2
nvidia-smi                           # must show the GPU, ~16 GB total, driver 570+
```

CUDA toolkit 12.8 (to build llama.cpp with GPU support):

```bash
# PC1 and PC2 (Ubuntu 24.04; for 22.04 replace ubuntu2404 with ubuntu2204)
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit-12-8
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
nvcc --version                       # must say release 12.8
```

**llama.cpp: build from source with CUDA.** The GitHub releases ship ready-made Windows CUDA zips, but for Linux only CPU/Vulkan builds, so on Ubuntu build it yourself (about 5–10 minutes). gpt-oss support (MXFP4) needs a build from August 2025 or later; the latest `master` is fine.

```bash
# PC1 and PC2
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j "$(nproc)" --target llama-server
~/llama.cpp/build/bin/llama-server --version
```

### ORCH (Windows, CPU only)

Python **3.12 or 3.13 from python.org** (tested on 3.13). Do **not** use an MSYS2/MinGW Python: it cannot install Streamlit.

```powershell
# ORCH (PowerShell)
winget install --id Git.Git -e
winget install --id Python.Python.3.13 -e
# open a NEW PowerShell window, then:
py -3.13 --version
git --version
curl.exe --version                   # ships with Windows 10 1803+ and 11
```

---

## 2. Getting the model

- **File:** `gpt-oss-20b-mxfp4.gguf` from the Hugging Face repo `ggml-org/gpt-oss-20b-GGUF`.
- **Size:** about 12 GB (check after download).
- **Where:** `~/models/` on **PC1 and PC2**. ORCH needs no model.

```bash
# PC1 and PC2
mkdir -p ~/models
python3 -m venv ~/hfenv
~/hfenv/bin/pip install -U huggingface_hub
~/hfenv/bin/hf download ggml-org/gpt-oss-20b-GGUF gpt-oss-20b-mxfp4.gguf --local-dir ~/models
# older huggingface_hub without the `hf` command:
#   ~/hfenv/bin/huggingface-cli download ggml-org/gpt-oss-20b-GGUF gpt-oss-20b-mxfp4.gguf --local-dir ~/models
ls -lh ~/models/gpt-oss-20b-mxfp4.gguf
```

Plain download, if you prefer:

```bash
wget -O ~/models/gpt-oss-20b-mxfp4.gguf https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-mxfp4.gguf
```

---

## 3. Starting the model server (PC1 and PC2)

### Try it in the foreground first

```bash
# PC1 and PC2
~/llama.cpp/build/bin/llama-server \
  -m ~/models/gpt-oss-20b-mxfp4.gguf \
  --alias gpt-oss-20b \
  --host 0.0.0.0 --port 8080 \
  -c 32768 -np 4 \
  -ngl 99 \
  -fa on \
  --jinja \
  --chat-template-kwargs '{"reasoning_effort":"low"}' \
  --metrics
```

| Flag | What it does | Why this value |
|---|---|---|
| `-m …gguf` | model file | the MXFP4 GGUF from step 2 |
| `--alias gpt-oss-20b` | model name the server reports | must equal `served_name` in `config/models.yaml` |
| `--host 0.0.0.0` | listen on all network interfaces | so ORCH can reach it; `127.0.0.1` would be local only |
| `--port 8080` | HTTP port | matches our endpoint config |
| `-c 32768` | **total** context, shared by all slots | 32768 / 4 slots = **8192 tokens per request**, fits next to the ~12 GB weights in 16 GB |
| `-np 4` | 4 parallel slots (requests at once) | must equal `slots` in the endpoint config / `LLM_SLOTS` |
| `-ngl 99` | put all layers on the GPU | the whole model fits in 16 GB |
| `-fa on` | flash attention | less memory, faster; newer builds default to `auto`. **Older builds take `-fa` without a value** — if `-fa on` is rejected, use `-fa` |
| `--jinja` | use the model's own chat template | required for gpt-oss's "harmony" format; without it reasoning leaks into answers |
| `--chat-template-kwargs '{"reasoning_effort":"low"}'` | default reasoning effort | gpt-oss cannot turn reasoning off; low keeps reasoning tokens small. Our client also sends this per request |
| `--metrics` | Prometheus metrics at `/metrics` | for measuring tokens/s (step 8) |

Continuous batching is on by default. Our client sends temperature, seed and `max_tokens` with every request.

It is ready when the log shows `server is listening on http://0.0.0.0:8080`. Check the per-slot context in the startup log (a line with `n_ctx_slot` / `n_ctx` per slot should say 8192). Stop it with Ctrl+C.

### Keep it running (systemd)

```bash
# PC1 and PC2
sudo tee /etc/systemd/system/llama-server.service >/dev/null <<'EOF'
[Unit]
Description=llama.cpp server (gpt-oss-20b)
After=network-online.target
Wants=network-online.target

[Service]
User=YOURUSER
ExecStart=/home/YOURUSER/llama.cpp/build/bin/llama-server -m /home/YOURUSER/models/gpt-oss-20b-mxfp4.gguf --alias gpt-oss-20b --host 0.0.0.0 --port 8080 -c 32768 -np 4 -ngl 99 -fa on --jinja --chat-template-kwargs '{"reasoning_effort":"low"}' --metrics
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo sed -i "s/YOURUSER/$USER/g" /etc/systemd/system/llama-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server
systemctl status llama-server --no-pager
journalctl -u llama-server -f        # live log (Ctrl+C to stop watching)
```

Alternative: Docker (`deploy/gpu-node/compose.yml`, needs Docker Engine + NVIDIA Container Toolkit; see section 10). It downloads the model itself and uses slightly different flags (see Gaps).

---

## 4. Networking

### Find each GPU PC's IP

```bash
# PC1 and PC2
hostname -I                          # first address, e.g. 192.168.1.10
```

Give both PCs a fixed address (DHCP reservation in the router) so the IPs don't change between runs.

### Open port 8080 on the GPU PCs (Ubuntu firewall, ufw)

The GPU PCs run Ubuntu, so the firewall is `ufw`, not Windows Firewall:

```bash
# PC1 and PC2: allow only our LAN to reach the model server
sudo ufw allow OpenSSH
sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp
sudo ufw enable
sudo ufw status
```

### Windows Firewall (only if a Windows machine must accept connections)

ORCH only makes **outgoing** connections to the GPU PCs, which Windows allows by default, so no rule is needed for that. You need an inbound rule only if teammates open the UI on ORCH from other machines (port 8501), or if you ever run a model server on Windows (port 8080):

```powershell
# ORCH, PowerShell as Administrator
New-NetFirewallRule -DisplayName "sudo_win UI 8501" -Direction Inbound -Protocol TCP -LocalPort 8501 -Action Allow -Profile Private
# only if a llama-server runs on this Windows machine:
New-NetFirewallRule -DisplayName "llama-server 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
```

### Test from ORCH to each server

```powershell
# ORCH (PowerShell)
Test-NetConnection 192.168.1.10 -Port 8080      # TcpTestSucceeded : True
Test-NetConnection 192.168.1.11 -Port 8080
curl.exe http://192.168.1.10:8080/health        # {"status":"ok"}
curl.exe http://192.168.1.11:8080/health
curl.exe http://192.168.1.10:8080/v1/models     # lists "gpt-oss-20b"
curl.exe http://192.168.1.11:8080/v1/models
```

`{"status":"ok"}` from both means the servers are up and reachable. While the model is still loading, `/health` returns an error status (503); wait and retry.

---

## 5. Project setup on ORCH

```powershell
# ORCH (PowerShell)
cd $HOME
git clone https://github.com/Lotr2/sudo_win.git
cd sudo_win
git checkout anans-approach
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q          # offline check, no model needed: all tests pass
```

To use `python` instead of `.\.venv\Scripts\python.exe`, activate the venv (once per window):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # once, if activation is blocked
.\.venv\Scripts\Activate.ps1
```

### Point the app at both GPU PCs

The app reads **environment variables or `config/endpoints.yaml`**; it does **not** read a `.env` file (see Gaps). Pick one of these.

**A. Environment variable (recommended, nothing to commit):**

```powershell
# ORCH, current PowerShell window only
$env:LLM_ENDPOINTS = "pc1=http://192.168.1.10:8080/v1,pc2=http://192.168.1.11:8080/v1"
$env:LLM_SLOTS = "4"            # = llama-server -np
$env:LLM_TIMEOUT_S = "300"

# ORCH, permanent for your user (open a new window afterwards)
[Environment]::SetEnvironmentVariable("LLM_ENDPOINTS", "pc1=http://192.168.1.10:8080/v1,pc2=http://192.168.1.11:8080/v1", "User")
[Environment]::SetEnvironmentVariable("LLM_SLOTS", "4", "User")
```

**B. Edit `config/endpoints.yaml`** (used when `LLM_ENDPOINTS` is not set). Filled-in example:

```yaml
endpoints:
  - name: pc1
    base_url: http://192.168.1.10:8080/v1
    models: ["gpt-oss-20b"]
    slots: 4
    timeout_s: 300
  - name: pc2
    base_url: http://192.168.1.11:8080/v1
    models: ["gpt-oss-20b"]
    slots: 4
    timeout_s: 300
down_for_s: 30           # a failed server is skipped for 30 s
wait_for_slot_s: 600     # how long a call may wait for a free slot
```

Don't commit your LAN IPs; `git update-index --skip-worktree config/endpoints.yaml` keeps your local edit out of commits.

### Model per role (`variants/anan/config.yaml`)

```yaml
models:
  default: gpt-oss-20b     # every role
  roles: {}                # per-role override, e.g.  verifier: gpt-oss-20b
budgets:
  default: {max_prompt_tokens: 4000, max_output_tokens: 1800}
  roles:
    builder: {max_prompt_tokens: 4500, max_output_tokens: 2800}
    verifier: {max_prompt_tokens: 2000, max_output_tokens: 400}
```

Model names must exist in `config/models.yaml`, whose `served_name` must equal the server's `--alias`:

```yaml
gpt-oss-20b:
  adapter: gpt_oss
  served_name: gpt-oss-20b
  reasoning_effort: low
  api_json_schema: false
```

Prompt + output per request must fit one slot (8192 tokens with `-c 32768 -np 4`); the builder's 4500 + 2800 = 7300 does.

---

## 6. Running it

### One run from the command line

```powershell
# ORCH, in the sudo_win folder, with LLM_ENDPOINTS set
.\.venv\Scripts\python.exe -m app.run --variant anan --brief demo\founder_brief_idea1.md
```

This runs idea1 (weak) → phase 2 STOP → the founder decisions file (`demo\founder_decisions.yaml`, used by default) pivots to idea2 → idea2 runs to phase 6. It prints each idea's result and a stats line (`llm_calls=… tokens=… json_retries=… truncations=… failed_steps=…`).

### The UI

```powershell
# ORCH
.\.venv\Scripts\python.exe -m streamlit run app\ui.py --server.address 0.0.0.0 --server.port 8501
```

Open **http://localhost:8501** on ORCH (or `http://<ORCH-IP>:8501` from another machine, after the firewall rule in step 4). In the sidebar:
1. Choose variant **anan**.
2. Leave "Fake LLM" **unticked**.
3. Click **Check model endpoints**; both pc1 and pc2 should be green.
4. Fill the brief form and press **Start run**.

The UI starts the orchestrator as a separate process. Approve or reject in the **Approvals** tab, and follow **Progress**, **Outputs** and **Outbox**. Start the UI from a window where `LLM_ENDPOINTS` is set, because the runs it starts inherit that environment.

### Where results go

| Started from | Folder |
|---|---|
| command line | `runs\anan\<UTC timestamp>\` |
| UI | `runs\ui\anan\<UTC timestamp>\` |

Inside each run folder:

| File / folder | Contents |
|---|---|
| `state.json` | everything completed so far (steps, gates, founder decisions, outward actions); the resume point |
| `outputs\` | `phase<N>_step<M>_<idea>-<name>.*`, gate files, `payment_test_log.md` |
| `decision_log.json` | one entry per agent action |
| `outbox\` | MOCKED publish/send results, one file per approved action |
| `product\<idea>\` | MVP feature files (code lives here, never in JSON) |
| `events.jsonl` | timestamped progress events (used by the UI and for timing) |
| `summary.json` | per-run stats: runtime, tokens, calls, JSON retries, truncations, failed steps, calls per server |
| `commerce_<idea>.json` | MOCKED orders, tokens, refunds, payouts |
| `approvals\` | UI approval queue (UI runs only) |
| `worker.log` | orchestrator output (UI runs only) |

`runs\` is gitignored. Only `--demo` writes the repo-root `outputs\` and `decision_log.json` that get committed; it **deletes the existing `outputs\` first** and refuses to run with the fake model.

### Resume after a crash, pause or reboot

```powershell
# ORCH
.\.venv\Scripts\python.exe -m app.run --resume runs\anan\20261117T150000Z
```

Completed steps are not recomputed, and outward actions (publish, send, open store, refund) never run twice. If the run paused waiting for a founder decision, add it to the decisions file it started with (or answer it in the UI) and resume. In the UI, open the run in the sidebar and press **Resume this run**.

### Several ideas in parallel (keeps both GPUs busy)

```powershell
# ORCH
.\.venv\Scripts\python.exe -m app.run --variant anan `
  --brief demo\founder_brief_idea2.md --brief tests\fixtures\brief_idea3.md `
  --decisions tests\fixtures\decisions_two_ideas.yaml
```

Each idea needs its own block in the decisions file; an idea without one pauses (command line) or waits in the Approvals tab (UI). Write new briefs as `demo\founder_brief_<id>.md` in the same format as the existing ones.

---

## 7. Smoke tests before a full run

```powershell
# ORCH, with LLM_ENDPOINTS set
.\.venv\Scripts\python.exe scripts\smoke_llm.py --variant anan
```

For each server it runs:
1. a short call;
2. one real "JSON step" in our format (metadata JSON + `---CONTENT---` + text), parsed and validated with the researcher schema;
3. a truncation check (`max_tokens=16`).

**Working looks like this, for both pc1 and pc2:**

```
== pc1  http://192.168.1.10:8080/v1
  [1] short call   OK   finish=stop answer='ready' reasoning_chars=… tokens=…
  [2] JSON step    OK   finish=stop fields=['claims', 'competitor_prices_usd', 'evidence_gaps', 'summary', 'title'] claims=2 …
  [3] truncation   OK   finish=length (max_tokens=16 must give 'length')
...
ALL CHECKS PASSED
```

What each part means:
- **`[1]`:** the server answers through our router and adapter. `reasoning_chars` above 0 means reasoning arrived *separately* from the answer, which is what we want.
- **`[2]`:** gpt-oss follows the output format. If it fails, the raw answer is printed; see step 9.
- **`[3]`:** cut-off answers are detected, so the pipeline will fail a step instead of using half an answer.

Raw single call without our code, to rule out the app:

```powershell
# ORCH
$body = @{ model = "gpt-oss-20b"; max_tokens = 200; temperature = 0
           messages = @(@{ role = "user"; content = "Reply with the single word: ready" })
           chat_template_kwargs = @{ reasoning_effort = "low" } } | ConvertTo-Json -Depth 5
$r = Invoke-RestMethod -Uri http://192.168.1.10:8080/v1/chat/completions -Method Post -ContentType "application/json" -Body $body
$r.choices[0].message.content; $r.choices[0].finish_reason; $r.usage
```

Then a full structural check of the pipeline itself, no GPU involved:

```powershell
# ORCH
.\.venv\Scripts\python.exe scripts\compare_variants.py --variant anan --fake-llm
```

---

## 8. What to measure on the first real run

| What | How | Where |
|---|---|---|
| Tokens/s per server | server log: each request prints prompt and generation speed (`… tokens per second`); or `curl http://localhost:8080/metrics` and read `llamacpp:predicted_tokens_seconds` / `llamacpp:prompt_tokens_seconds` | PC1, PC2 (`journalctl -u llama-server -f`) |
| VRAM use | `nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv -l 5 \| tee ~/vram.csv` during the run; peak `memory.used` must stay below ~15.5 GB | PC1, PC2 |
| JSON failure rate | `summary.json` → `stats.json_retries` ÷ `stats.llm_calls`; `stats.failed_steps` lists steps that failed after the retry | ORCH, run folder |
| Truncations | `summary.json` → `stats.truncations`; failed step files contain `finish_reason=length` | ORCH |
| Prompts trimmed to budget | `summary.json` → `stats.trimmed_prompts` | ORCH |
| Wall-clock | `summary.json` → `runtime_s` (whole run); per step from `events.jsonl` (snippet below) | ORCH |
| Load split across GPUs | `summary.json` → `endpoints.pc1.served` / `endpoints.pc2.served` (+ `failures`) | ORCH |
| Full scorecard | `.\.venv\Scripts\python.exe scripts\compare_variants.py --variant anan` → `variants\anan\scorecard.md`, `.planning\COMPARISON.md` | ORCH |

Per-step durations from `events.jsonl`:

```powershell
# ORCH: replace the run folder
$run = "runs\anan\20261117T150000Z"
@'
import json, sys
start = {}
for line in open(sys.argv[1] + "/events.jsonl", encoding="utf-8"):
    e = json.loads(line)
    if e["type"] != "step": continue
    k = (e["idea"], e["step"])
    if e["status"] == "running": start[k] = e["t"]
    elif k in start: print(f"{e['idea']:6} {e['step']:14} {e['t'] - start.pop(k):7.1f}s  {e['status']}")
'@ | .\.venv\Scripts\python.exe - $run
```

Record the results (GPU model, tokens/s, peak VRAM, JSON retry rate, truncations, wall-clock) in `.planning/PROGRESS.md`.

---

## 9. Troubleshooting

| Problem | What you see | Fix |
|---|---|---|
| **Out of memory** on a GPU PC | server exits at start or on load with `cudaMalloc failed` / `out of memory`; `nvidia-smi` near 16 GB | In order: (1) close other GPU users, such as the desktop session (check `nvidia-smi`); (2) fewer slots: `-np 2 -c 16384` (keeps 8192/request) and `LLM_SLOTS=2`; (3) quantized KV cache: add `-ctk q8_0 -ctv q8_0` (needs flash attention); (4) last resort: `--n-cpu-moe 4` moves some expert layers to the CPU (works, but slower) |
| **Server not reachable** | `Test-NetConnection … TcpTestSucceeded : False`; run fails with `model unavailable … all endpoints failed` | Server running? (`systemctl status llama-server`). Started with `--host 0.0.0.0`? (`ss -ltnp \| grep 8080` must show `0.0.0.0:8080`). Firewall (`sudo ufw status`). IP changed? (`hostname -I`). Same subnet, no VPN on ORCH? |
| **Broken JSON** | `json_retries` high; step files say `STEP FAILED … invalid answer after 2 attempts` | Make sure the server runs with `--jinja` (otherwise harmony text leaks). Run `scripts\smoke_llm.py` and read the printed raw answer. Raise `retries: 2` in `variants\anan\config.yaml`. Tighten the role's prompt (example answer shape is in `variants\anan\prompts\`) |
| **Cut-off answers** | `truncations` > 0; step files contain `finish_reason=length` | Raise that role's `max_output_tokens` in `variants\anan\config.yaml`, keeping prompt + output ≤ 8192 (or ≤ `-c`/`-np`). Check reasoning effort is low (server log / `reasoning_chars` in smoke test). Lower the prompt's word limit for that role |
| **Request too long** | step fails with `HTTP 400 … context` | prompt + `max_output_tokens` exceeds the slot context: lower `max_prompt_tokens` for that role, or give more context per slot (`-c 32768 -np 3` → 10922 per slot, set `LLM_SLOTS=3`) |
| **One server down** | `summary.json` → that endpoint has `failures` > 0 | Nothing to do mid-run: the router skips it for 30 s and sends calls to the other PC. Restart it (`sudo systemctl restart llama-server`). If both were down, steps fail; fix the servers and `--resume` |
| **Run paused** | idea status `paused`; log entry `awaiting:<action>` | a founder decision is missing: add it to the decisions file (or answer in the UI) and `--resume` |
| **Slow first call** | first request takes much longer | model loading/warm-up; check that `/health` says ok before starting runs |
| **Python errors on ORCH** | `pip` fails building `pyarrow` | you are on MSYS2/MinGW Python; install python.org Python and recreate `.venv` |
| **UI shows no progress** | Progress tab empty | open the run's `worker.log` (shown under "Orchestrator error" if it crashed); check `LLM_ENDPOINTS` was set in the window that started the UI |

---

## 10. Single-PC mode (everything on one GPU PC, Ubuntu)

### Option A: native (same steps, one machine)

On the GPU PC:
1. Do sections 1–3, with `--host 127.0.0.1` if nothing else needs to reach it.
2. Install Python and set up the project:

```bash
# SINGLE (Ubuntu 24.04 ships Python 3.12)
cd ~
git clone https://github.com/Lotr2/sudo_win.git
cd sudo_win
git checkout anans-approach
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_llm.py --variant anan       # default endpoint: http://localhost:8080/v1
.venv/bin/python -m app.run --variant anan --brief demo/founder_brief_idea1.md
.venv/bin/python -m streamlit run app/ui.py --server.port 8501   # then open http://localhost:8501
```

No `LLM_ENDPOINTS` is needed: `config/endpoints.yaml` defaults to one local server. Set `LLM_SLOTS` if you changed `-np`.

### Option B: Docker, one command

```bash
# SINGLE: Docker Engine + NVIDIA Container Toolkit (once)
curl -fsSL https://get.docker.com | sudo sh
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# SINGLE: run (first start downloads the model, ~12 GB)
cd ~/sudo_win
sudo docker compose up
```

Open **http://localhost:8501** and pick variant **anan** in the sidebar. The same Docker files run the two-PC setup: `deploy/gpu-node/compose.yml` on PC1/PC2, and `deploy/orchestrator/compose.yml` on a machine with Docker (on Windows that means Docker Desktop with WSL2).

If your single GPU PC runs **Windows** instead: download the two zips `llama-<build>-bin-win-cuda-12.4-x64.zip` and `cudart-llama-bin-win-cuda-12.4-x64.zip` from https://github.com/ggml-org/llama.cpp/releases, unzip both into one folder, and run `llama-server.exe` with the same flags as section 3 (PowerShell: `--chat-template-kwargs '{\"reasoning_effort\":\"low\"}'`). The rest is section 5–6 with `LLM_ENDPOINTS` left unset.

---

## Gaps: where the code does not match this guide yet

1. **No `.env` loading.** The Python app reads only real environment variables (`LLM_ENDPOINTS`, `LLM_SLOTS`, `LLM_TIMEOUT_S`) or `config/endpoints.yaml`. `.env` / `.env.example` are used only by Docker Compose.
2. **One slot count for all servers via env.** `LLM_SLOTS` applies to every endpoint in `LLM_ENDPOINTS`. Different `-np` per PC needs `config/endpoints.yaml`.
3. **Docker files use different server flags than section 3.** They download with `-hf` instead of using `~/models`, and don't pass `--metrics`, `-fa` or `--chat-template-kwargs` (reasoning effort is still sent per request by our client; flash attention defaults to `auto` in recent builds).
4. **UI ignores `ARCH_VARIANT`.** The UI's variant selector always starts on `_template`; pick `anan` by hand. (`ARCH_VARIANT` only sets the CLI default.)
5. **UI pivot without a decisions file.** If the founder chooses "pivot" in the UI, the run stops there: there is no field to give the next brief (open task in `.planning/PROGRESS.md`).
6. **No built-in timing or GPU metrics.** Tokens/s and VRAM come from the server log, `/metrics` and `nvidia-smi`; per-step timing needs the `events.jsonl` snippet in step 8.
7. **Untested on real hardware.** Everything above was verified only with fake endpoints and `docker compose config`: the llama.cpp flags, the 8192-token slot budget, low reasoning effort, MXFP4 memory use and the JSON format all need the first real run. Also untested:
   - temperature 0 with gpt-oss (OpenAI suggests 1.0 for this model; change `temperature` in the variant config if answers repeat themselves);
   - the server-side JSON constraint (`api_json_schema`), which stays off.
8. **Fixed waiting times.** An approval waits at most 1 hour in the UI queue before the run pauses, and a call waits at most 10 minutes for a free slot (`wait_for_slot_s`). These are not exposed in the UI.
