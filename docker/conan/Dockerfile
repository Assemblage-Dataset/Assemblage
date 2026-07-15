# escape=`

# DeepHistory Legacy Builder - Conan-based Windows binary builder
# Extends the base Assemblage Windows image with Conan package manager
# for building multi-version C/C++ libraries.

FROM mcr.microsoft.com/windows/servercore:ltsc2022

WORKDIR C:/tools

SHELL ["cmd", "/S", "/C"]

ENV VS_VERSION=2022

# Install Visual Studio Build Tools 2022
ADD https://aka.ms/vs/17/release/vs_BuildTools.exe vs_buildtools.exe
RUN start /w vs_buildtools.exe --wait --quiet --norestart --nocache `
    --installPath "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools" `
    --add Microsoft.VisualStudio.Workload.VCTools `
    --add Microsoft.VisualStudio.Workload.MSBuildTools `
    --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    --add Microsoft.VisualStudio.Component.Windows10SDK.19041 `
    --add Microsoft.VisualStudio.Component.VC.CMake.Project `
    --add Microsoft.Net.Component.4.8.SDK `
    --add Microsoft.Net.Component.4.8.TargetingPack `
    --includeRecommended

SHELL ["powershell", "-Command"]

# Install Python 3.12
ENV python_version=3.12.3
ADD https://www.python.org/ftp/python/${python_version}/python-${python_version}-amd64.exe python3.exe
RUN Start-Process 'python3.exe' -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1' -Wait; `
    Remove-Item 'python3.exe' -Force

# Install Git
ENV git_version=2.51.0
ADD https://github.com/git-for-windows/git/releases/download/v${git_version}.windows.2/Git-${git_version}.2-64-bit.exe git-installer.exe
RUN Start-Process git-installer.exe -ArgumentList '/VERYSILENT', '/NORESTART' -Wait; `
    Remove-Item git-installer.exe -Force

# Install Ninja build system (needed by Conan for some packages)
ADD https://github.com/niceBuild/ninja-win/releases/download/v1.12.1/ninja-win.zip ninja.zip
RUN Expand-Archive -Path ninja.zip -DestinationPath C:/tools/ninja; `
    Remove-Item ninja.zip

# Install Universal Ctags
ENV ctags_version=6.1.0
ADD https://github.com/universal-ctags/ctags-win32/releases/download/v${ctags_version}/ctags-v${ctags_version}-x86.zip ctags.zip
RUN Expand-Archive -Path ctags.zip -DestinationPath ctags; `
    Remove-Item ctags.zip

# Register DIA2 SDK for PDB extraction
RUN "C:\\Windows\\System32\\regsvr32.exe /s \"C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\DIA SDK\\bin\\amd64\\msdia140.dll\""

# Install Conan 2.x
RUN python.exe -m pip install --upgrade pip setuptools wheel
RUN python.exe -m pip install conan>=2.0

# Configure Conan - detect MSVC profile and add conancenter remote
RUN conan profile detect --force; `
    conan remote add conancenter https://center2.conan.io --force

# Set up paths
ENV PYTHONPATH=C:\app
COPY ./backend C:\app
COPY ./backend/assemblage/windows/dia2dump C:\tools\Dia2Dump

RUN setx path "`$env:PATH;C:\tools\Dia2Dump;C:\tools\ctags;C:\tools\ninja"

ENV PIP_USE_PEP517=1
RUN python.exe -m pip install --prefer-binary -r C:\app\requirements.txt

ENV COMPILER="MSVC"
ENV LANGUAGE="c++"
ENV TYPE="legacy_conan"

WORKDIR C:\app

# Default: run the DeepHistory builder
ENTRYPOINT ["powershell", "-Command", `
    "& 'C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\Common7\\Tools\\Launch-VsDevShell.ps1' -Arch amd64; ", `
    "python.exe scripts/build_deephistory.py @args"]
