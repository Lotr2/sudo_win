# Diagrams

- `anan-workflow.mmd` — source (Mermaid) of anan's workflow: six phases, steps by role, gates, founder approvals (✋), pivot, router over two GPU PCs.
- `anan-workflow.svg` — rendered, scalable (open in a browser).
- `anan-workflow.png` — rendered at 2x for slides and chat.

Re-render after editing the `.mmd` (Node.js 18+; uses your installed Chrome instead of downloading one):

```powershell
mkdir $env:TEMP\mmd; cd $env:TEMP\mmd
$env:PUPPETEER_SKIP_DOWNLOAD = "true"; npm init -y; npm install @mermaid-js/mermaid-cli
'{ "executablePath": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" }' | Set-Content puppeteer.json
'{ "theme": "base", "themeVariables": { "fontFamily": "Segoe UI, Inter, Helvetica, Arial, sans-serif", "fontSize": "15px", "lineColor": "#94A3B8" }, "flowchart": { "curve": "basis", "useMaxWidth": false } }' | Set-Content config.json
.\node_modules\.bin\mmdc -i <repo>\docs\diagrams\anan-workflow.mmd -o <repo>\docs\diagrams\anan-workflow.svg -c config.json -p puppeteer.json -b white
.\node_modules\.bin\mmdc -i <repo>\docs\diagrams\anan-workflow.mmd -o <repo>\docs\diagrams\anan-workflow.png -c config.json -p puppeteer.json -b white -s 2
```

The two `~~~` links at the end of the source only keep the legend and infrastructure boxes above the flow; `linkStyle 37,38` hides them. If you add or remove edges above them, update those two indices.
