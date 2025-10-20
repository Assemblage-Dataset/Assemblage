# setup ms build and run python script
# Required to set up vs code compiler and then run python script
  
param(
    [string]$Version = $env:VS_VERSION
)
 
function Invoke-VcVars {
    param(
        [string]$Version = "2022"
    )
    cmd /c "`"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat`" && set" | 
    ForEach-Object {
        if ($_ -match "^(.*?)=(.*)$") {
            Set-Item -Force -Path "ENV:\$($matches[1])" -Value $matches[2]
        }
    }
}

Invoke-VcVars -VSVersion $Version

python.exe C:\\app\\scripts\\start_worker.py