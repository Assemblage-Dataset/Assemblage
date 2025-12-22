# setup ms build and run python script
# Required to set up vs code compiler and then run python script
  
param(
    [string]$Version = $env:VS_VERSION
)
 
function Invoke-VcVars {
    param(
        [string]$Version = "2022"
    )
    $batPath = "C:\Program Files (x86)\Microsoft Visual Studio\$Version\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    cmd /c "`"$batPath`" && set" | 
    ForEach-Object {
        if ($_ -match "^(.*?)=(.*)$") {
            Set-Item -Force -Path "ENV:\$($matches[1])" -Value $matches[2]
        }
    }
}

# Invoke-VcVars -Version $Version
Invoke-VcVars

$env:PATH = "C:\tools\ctags;" + $env:PATH


$diaDlls = @(
    "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\DIA SDK\bin\amd64\msdia140.dll",
    "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\DIA SDK\bin\x86\msdia140.dll"
)

foreach ($dll in $diaDlls) {
    if (Test-Path $dll) {
        Write-Host "Registering $dll ..."
        Start-Process -FilePath "C:\Windows\System32\regsvr32.exe" `
                      -ArgumentList "/s", "`"$dll`"" `
                      -Wait -NoNewWindow
    } else {
        Write-Warning "DIA DLL not found: $dll"
    }
}


python.exe C:\app\scripts\start_worker.py



