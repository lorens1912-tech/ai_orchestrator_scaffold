$ProjectRoot = "C:\AI\ai_orchestrator_scaffold"

function cda { Set-Location $ProjectRoot }

function gate { powershell -ExecutionPolicy Bypass -File "$ProjectRoot\tools\gate.ps1" }

function ta { cda; python -m unittest discover -s tests -p "test_0*.py" -v }

function t3 { cda; python -m unittest -v tests.test_003_write_step }
function t4 { cda; python -m unittest -v tests.test_004_critic_step }
function t5 { cda; python -m unittest -v tests.test_005_edit_step }
function t6 { cda; python -m unittest -v tests.test_006_pipeline_smoke }
function t7 { cda; python -m unittest -v tests.test_007_artifact_schema }

function modes { Invoke-RestMethod http://127.0.0.1:8000/config/validate | Select-Object -ExpandProperty mode_ids }

function step_write  { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/step -ContentType "application/json" -Body '{"mode":"WRITE","preset":"DEFAULT","input":"quick write"}' }
function step_critic { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/step -ContentType "application/json" -Body '{"mode":"CRITIC","preset":"DEFAULT","input":"quick critic"}' }
function step_edit   { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/step -ContentType "application/json" -Body '{"mode":"EDIT","preset":"DEFAULT","input":"quick edit"}' }
