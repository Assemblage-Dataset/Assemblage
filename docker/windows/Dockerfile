# escape=`

# Use the microsoft dotnet framework docker image
# Use the microsoft dotnet framework docker image
FROM mcr.microsoft.com/windows/servercore:ltsc2022
# FROM mcr.microsoft.com/dotnet/framework/sdk:4.8-windowsservercore-ltsc2022
WORKDIR C:/tools
WORKDIR C:/tools
# Restore the default Windows shell for correct batch processing.
SHELL ["cmd", "/S", "/C"]


ADD https://aka.ms/vs/17/release/vs_BuildTools.exe vs_buildtools.exe 
# Install Build Tools with the Microsoft.VisualStudio.Workload.AzureBuildTools workload, excluding workloads and components with known issues.
RUN (start /w vs_buildtools.exe --wait --quiet --norestart --nocache `
    --installPath "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools" `
    --add Microsoft.VisualStudio.Workload.VCTools `
    --add Microsoft.Net.Component.4.8.SDK `
    --add Microsoft.Net.Component.4.8.TargetingPack `
    --add Microsoft.Component.MSBuild `
    --add Microsoft.VisualStudio.Workload.MSBuildTools `
    --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended `
    --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    --add Microsoft.VisualStudio.Component.Windows10SDK.19041 `
    --add Microsoft.VisualStudio.Component.VC.CMake.Project `
    --add Microsoft.VisualStudio.ComponentGroup.NativeDesktop.Win81 `
    --add Microsoft.VisualStudio.ComponentGroup.NETFramework `
    --remove Microsoft.VisualStudio.Component.Windows10SDK.10240 `
    --remove Microsoft.VisualStudio.Component.Windows10SDK.10586 `
    --remove Microsoft.VisualStudio.Component.Windows10SDK.14393 `
    --remove Microsoft.VisualStudio.Component.Windows81SDK `
    --includeRecommended `
    || IF "%ERRORLEVEL%"=="3010" EXIT 0) 
# && del /q vs_buildtools.exe

SHELL ["powershell", "-Command"]

ENV python_version=3.12.3 
ADD https://www.python.org/ftp/python/${python_version}/python-${python_version}-amd64.exe python3.exe


RUN Start-Process 'python3.exe' -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1' -Wait; `
    Remove-Item 'python3.exe' -Force

ENV git_version=2.51.0

ADD https://github.com/git-for-windows/git/releases/download/v${git_version}.windows.2/Git-${git_version}.2-64-bit.exe git-installer.exe

RUN Start-Process git-installer.exe -ArgumentList '/VERYSILENT', '/NORESTART' -Wait; `
    Remove-Item git-installer.exe -Force


# ENV cmake_version=4.1.2
# ADD https://github.com/Kitware/CMake/releases/download/v4.1.2/cmake-4.1.2-windows-x86_64.msi cmake.msi

# RUN powershell -Command `
#     msiexec /i C:/cmake.msi /quiet /norestart

ENV ctags_version=6.1.0

ADD https://github.com/universal-ctags/ctags-win32/releases/download/v${ctags_version}/ctags-v${ctags_version}-x86.zip ctags.zip


RUN Expand-Archive -Path ctags.zip -DestinationPath ctags; `
    Remove-Item ctags.zip

# RUN delete all the installeres later    


# Register dll file for dia2dump

RUN "Start-Process regsvr32 -ArgumentList '/s', 'C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\Community\\DIA SDK\\bin\\msdia140.dll' `
    -Wait -NoNewWindow -PassThru | Wait-Process"



RUN "Start-Process regsvr32 -ArgumentList '/s', 'C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildCommunityTools\\DIA SDK\\bin\\amd64\\msdia140.dll' `
    -Wait -NoNewWindow -PassThru | Wait-Process"





# add to path ( note may need to create a script that does this at run time)
ENV PYTHONPATH=C:\app

# #
COPY ./backend C:\app

WORKDIR C:\app
ENV PIP_USE_PEP517=1
RUN python.exe -m pip install --upgrade pip setuptools wheel

RUN python.exe -m pip install --prefer-binary -r requirements.txt
# COPY ./aws C:\Administrator\.aws

# ENV AWS_CONFIG_FILE="C:\\Administrator\\.aws\\config"
# ENV AWS_SHARED_CREDENTIALS_FILE="C:\\Administrator\\.aws\\credentials"E
ENV PYTHONPATH="C:\\app;"
ENV COMPILER="MSVC"
ENV LANGUAGE="c++"
# maybe need to figure out adding ^^ to path via ps1 script ( so might need to add script in this folder to 
# add that to path as then run python.exe etc)]

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 CMD powershell -Command "Get-Process | Where-Object CommandLine -like '*start_worker.py*' | Select-Object -First 1; if ($?) { exit 0 } else { exit 1 }"

ENTRYPOINT ["powershell", "-File", "C:\\app\\scripts\\start_windows_worker.ps1"]
# Define the entry point for the docker container.
# This entry point starts the developer command prompt and launches the PowerShell shell.
# ENTRYPOINT ["C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat", "&&", "powershell.exe", "-NoLogo", "-ExecutionPolicy", "Bypass"]



