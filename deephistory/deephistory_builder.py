#!/usr/bin/env python3
"""
DeepHistory Linux Builder

Reads packages.json and builds each package at the specified commit
with the configured compiler and optimization flag. Uploads binaries
and metadata to MinIO.

Standalone -- no RabbitMQ or coordinator needed.

Env vars:
    COMPILER        gcc | clang  (default: gcc)
    COMPILER_FLAG   -O0 | -O1 | -O2 | -O3  (default: -O2)
    S3_HOST         MinIO hostname  (default: minio)
    S3_PORT         MinIO port      (default: 9000)
    S3_ACCESS_KEY / S3_SECRET_ACCESS_KEY
    DATA_DIR        where packages.json + progress/ live  (default: /data)
"""

import json
import os
import re
import sys
import logging
import shutil
import glob
import time

sys.path.insert(0, '/app')

from assemblage.build.linux import LinuxBuildStrategy
from assemblage.storage.s3 import S3Client, S3Bucket
from assemblage.consts import BuildStatus, CloneStatus
from assemblage.build.detect import get_build_system

logger = logging.getLogger('deephistory')

# Each replica gets its own clone directory to avoid race conditions
# when multiple replicas build different versions of the same package
import socket
BASE_PATH = f'/tmp/deephistory-{socket.gethostname()}'
DATA_DIR = os.environ.get('DATA_DIR', '/data')


# Per-package cmake overrides for projects that need special flags
_PACKAGE_CMAKE_OVERRIDES = {
    'ada':  '-DADA_BENCHMARKS=OFF -DADA_TESTING=OFF -DADA_TOOLS=OFF',
    'assimp': '-DASSIMP_BUILD_TESTS=OFF -DASSIMP_WARNINGS_AS_ERRORS=OFF',
    'benchmark': '-DBENCHMARK_ENABLE_TESTING=OFF',
    'amqp-cpp': '-DAMQP-CPP_BUILD_SHARED=ON',
    'ceres-solver': '-DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF -DBUILD_BENCHMARKS=OFF -DMINIGLOG=ON -DSUITESPARSE=OFF -DCXSPARSE=OFF -DACCELERATESPARSE=OFF',
    'jasper': '-DJAS_ENABLE_DOC=OFF -DJAS_ENABLE_PROGRAMS=OFF -DJAS_ENABLE_OPENGL=OFF',
    'lief': '-DLIEF_PYTHON_API=OFF -DLIEF_EXAMPLES=OFF -DLIEF_TESTS=OFF',
    'libavif': '-DAVIF_CODEC_AOM=LOCAL -DAVIF_LIBYUV=LOCAL -DAVIF_LIBSHARPYUV=LOCAL -DAVIF_BUILD_APPS=OFF -DAVIF_BUILD_TESTS=OFF',
    'aeron': '-DBUILD_AERON_DRIVER=ON -DC_WARNINGS_AS_ERRORS=OFF -DCXX_WARNINGS_AS_ERRORS=OFF -DAERON_TESTS=OFF -DAERON_BUILD_SAMPLES=OFF',
    'fast-dds': '-DTHIRDPARTY=FORCE -DSECURITY=OFF -DEPROSIMA_BUILD_TESTS=OFF -DCOMPILE_EXAMPLES=OFF',
    'embree3': '-DEMBREE_TUTORIALS=OFF -DEMBREE_ISPC_SUPPORT=OFF',
    'lightgbm': '-DBUILD_CLI=OFF -DUSE_OPENMP=OFF -DBUILD_TESTING=OFF',
    'onnx': '-DONNX_USE_PROTOBUF_SHARED_LIBS=ON -DONNX_BUILD_TESTS=OFF -DONNX_BUILD_BENCHMARKS=OFF -DProtobuf_USE_STATIC_LIBS=OFF',
    'uriparser': '-DURIPARSER_BUILD_TESTS=OFF -DURIPARSER_BUILD_DOCS=OFF',
    'draco': '-DCMAKE_CXX_STANDARD=14',
    'cppcommon': '-DCPPCOMMON_MODULE=OFF',
    'json-schema-validator': '-DJSON_VALIDATOR_BUILD_TESTS=OFF',
    'openmesh': '-DOPENMESH_BUILD_APPS=OFF',
    'proj': '-DBUILD_TESTING=OFF -DBUILD_APPS=OFF',
    'libzen': '-DBUILD_SHARED_LIBS=ON',
    'libzippp': '-DLIBZIPPP_BUILD_TESTS=OFF',
    'djinni-support-lib': '-DDJINNI_WITH_JNI=ON -DDJINNI_STATIC_LIB=ON',
    'taglib': '-DCMAKE_CXX_STANDARD=17 -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF',
    'log4cxx': '-DBUILD_TESTING=OFF -DLOG4CXX_INSTALL_PDB=OFF',
    'sobjectizer': '-DSOBJECTIZER_BUILD_STATIC=ON -DSOBJECTIZER_BUILD_SHARED=ON',
    'spirv-tools': '-DSPIRV_SKIP_EXECUTABLES=ON -DSPIRV_SKIP_TESTS=ON -DSPIRV-Headers_SOURCE_DIR=/opt/SPIRV-Headers',
    'rotor': '-DBUILD_TESTING=OFF -DBUILD_BOOST_ASIO=OFF -DBUILD_BOOST=OFF',
    're2': '-DRE2_BUILD_TESTING=OFF -DBUILD_TESTING=OFF',
    'vvenc': '-DVVENC_ENABLE_WERROR=OFF -DVVENC_ENABLE_LINK_TIME_OPT=OFF',
}

# Packages with CMakeLists.txt in a non-root subdirectory
_PACKAGE_CMAKE_SUBDIR = {
    'antlr4-cppruntime': 'runtime/Cpp',
    'ags': 'Engine',
    'clipper2': 'CPP',
    'clipper': 'CPP',
    'expat': 'expat',
    'zstd': 'build/cmake',
    'protobuf': 'cmake',
    'lz4': 'build/cmake',
    'libzen': 'Project/CMake',
    'sobjectizer': 'dev',
}

# Packages that use configure.py instead of cmake/autotools
_CONFIGURE_PY_PACKAGES = {'botan'}

# Force a specific build system (override auto-detection)
_PACKAGE_FORCE_BUILD_SYSTEM = {
    'bzip3': 'bootstrap',      # has configure.ac + Makefile.am, no configure
    'xz_utils': 'configure',   # old commits have no CMakeLists.txt
    'boost': 'bootstrap',      # uses b2/bjam via bootstrap.sh
    'flac': 'configure',       # cmake needs libogg; autotools works standalone
    'apr-util': 'configure',   # has Makefile.in but needs configure first
    'calceph': 'configure',    # autotools project
    'libfdk_aac': 'configure', # autotools (has CMakeLists but configure is better)
    'libmp3lame': 'configure', # autotools only
    'libsodium': 'configure',  # autotools
    'libusb': 'configure',     # autotools
    'mpdecimal': 'configure',  # autotools
    'pcre': 'cmake',           # cmake works, tag checkout fixed by is_hash fix
    'libressl': 'configure',   # cmake available but autotools more reliable
    'libpng': 'configure',     # autotools build
    'taglib': 'cmake',         # force cmake path
    'libpq': 'configure',     # PostgreSQL: cmake doesn't build libpq properly
    'lua': 'make',             # plain Makefile
    'sqlite3': 'configure',   # autotools works better than cmake here
    'log4cxx': 'cmake',       # has cmake
    'c-blosc': 'cmake',       # cmake
    'highway': 'cmake',       # cmake
    'cppcommon': 'cmake',     # cmake
    'bdwgc': 'configure',     # autotools
    'djinni-support-lib': 'cmake',
    'zimg': 'configure',      # autotools (configure.ac + Makefile.am); graphengine subdir misleads auto-detect
    'lz4': 'cmake',           # cmake in build/cmake subdir
    'libzen': 'cmake',        # cmake in Project/CMake subdir
    'mozjpeg': 'configure',   # cmake says "platform not supported, use autotools"
    'tcl': 'configure',       # no CMakeLists (autotools project)
    'ags': 'make',            # use Engine/Makefile directly — cmake is broken
}

# Header-only libraries: (header_file, impl_define, extra_sources)
_HEADER_ONLY_PACKAGES = {
    'cgltf':  ('cgltf.h', 'CGLTF_IMPLEMENTATION', []),
    'drwav':  ('dr_wav.h', 'DR_WAV_IMPLEMENTATION', []),
}

# Packages with raw .cpp files, no build system: {name: [source_files]}
_RAW_CPP_PACKAGES = {
    'imgui': ['imgui.cpp', 'imgui_draw.cpp', 'imgui_tables.cpp', 'imgui_widgets.cpp'],
    # implot needs imgui headers - handled separately in run_build
    # 'implot': ['implot.cpp', 'implot_items.cpp'],
    'qr-code-generator': ['cpp/QrCode.cpp'],
    'quirc': ['lib/decode.c', 'lib/identify.c', 'lib/quirc.c', 'lib/version_db.c'],
    'http_parser': ['http_parser.c'],
    'pthreadpool': ['src/threadpool-pthreads.c', 'src/memory.c'],
}


class DeepHistoryBuildStrategy(LinuxBuildStrategy):
    """LinuxBuildStrategy with BUILD_SHARED_LIBS=ON for cmake projects.

    Library-only projects (abseil, fmt, etc.) produce only .a static archives
    by default. Adding BUILD_SHARED_LIBS=ON makes cmake produce .so shared
    libraries which are ET_DYN and captured by find_binaries().
    """

    def __init__(self, *args, package_name=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._package_name = package_name

    @staticmethod
    def _strip_werror(clone_dir):
        """Remove -Werror from cmake files so older code builds with newer compilers.

        Carefully handles cases like add_cxx_compiler_flag(-Werror) by
        commenting out the entire line instead of leaving empty args.
        """
        import re
        for root, dirs, files in os.walk(clone_dir):
            dirs[:] = [d for d in dirs if d != '.git']
            for fn in files:
                if fn.endswith(('.cmake', '.txt')) and 'cmake' in fn.lower() or fn == 'CMakeLists.txt':
                    fpath = os.path.join(root, fn)
                    try:
                        with open(fpath, 'r', errors='replace') as f:
                            content = f.read()
                        new = content
                        # Comment out entire lines that pass -Werror as a function arg
                        # e.g. add_cxx_compiler_flag(-Werror) or target_compile_options(... -Werror)
                        new = re.sub(r'^([^#]*\([^)]*)-Werror\b([^)]*\))',
                                     r'# stripped: \1\2', new, flags=re.MULTILINE)
                        # Strip standalone -Werror in flag strings (e.g. set(FLAGS "-Wall -Werror"))
                        new = re.sub(r'(?<=["\s])-Werror(?!=)\b', '', new)
                        new = re.sub(r'/WX\b', '', new)
                        if new != content:
                            with open(fpath, 'w') as f:
                                f.write(new)
                    except Exception:
                        pass

    def _find_cmake_source_dir(self, clone_dir):
        """Find the directory containing CMakeLists.txt.

        Checks package-specific overrides first, then common subdirectories.
        """
        # Check package-specific override
        if self._package_name and self._package_name in _PACKAGE_CMAKE_SUBDIR:
            subdir = _PACKAGE_CMAKE_SUBDIR[self._package_name]
            path = os.path.join(clone_dir, subdir)
            if os.path.isfile(os.path.join(path, 'CMakeLists.txt')):
                return path

        root_cmake = os.path.join(clone_dir, 'CMakeLists.txt')
        if os.path.isfile(root_cmake):
            return clone_dir

        # Common subdirectory patterns for C++ runtime builds
        candidates = [
            'runtime/Cpp', 'cpp', 'src', 'lib', 'c', 'c++',
        ]
        for subdir in candidates:
            path = os.path.join(clone_dir, subdir, 'CMakeLists.txt')
            if os.path.isfile(path):
                return os.path.join(clone_dir, subdir)
        return clone_dir

    def find_binaries(self, path):
        """Extended binary finder: also captures .a/.so in hidden dirs.

        The parent find_binaries skips .libs/ (libtool output) and
        thirdparty/*/build/ (ExternalProject). We walk everything
        except .git to catch all produced libraries.
        """
        bins = super().find_binaries(path)
        # Walk ALL dirs including hidden ones like .libs/ (libtool)
        for root, dirs, fnames in os.walk(os.path.realpath(path)):
            # Only skip .git
            dirs[:] = [d for d in dirs if d != '.git']
            if '/CMakeFiles/' in root:
                continue
            for fn in fnames:
                fp = os.path.join(root, fn)
                if fn.endswith('.a') and fn.startswith('lib'):
                    bins.add(fp)
                elif '.so' in fn and fn.startswith('lib'):
                    bins.add(fp)
        return bins

    def run_build(self, repo, clone_dir, compiler_flag="", slnfile=None):
        files = [f.split("/")[-1]
                 for f in glob.iglob(clone_dir + '**/**', recursive=True)]
        build_tool = get_build_system(files)

        # Override build system detection for specific packages
        if self._package_name in _PACKAGE_FORCE_BUILD_SYSTEM:
            build_tool = _PACKAGE_FORCE_BUILD_SYSTEM[self._package_name]
            logger.info("Forced build system for %s: %s",
                        self._package_name, build_tool)

        debug_flag = "-g"
        ndebug_flag = "-DNDEBUG"
        wno_error = "-Wno-error"
        cmake_build_type = "RelWithDebInfo"
        # C flags — preemptive includes for GCC 13/Clang 16+ compat
        c_flags = " ".join(
            f for f in [debug_flag, ndebug_flag, wno_error,
                        # Clang 16+ promotes these to errors by default; restore to warnings
                        "-Wno-error=implicit-function-declaration",
                        "-Wno-error=implicit-int",
                        "-Wno-error=int-conversion",
                        "-Wno-error=incompatible-pointer-types",
                        "-include stdint.h", "-include limits.h",
                        "-D_GNU_SOURCE",  # enable dl_iterate_phdr etc.
                        compiler_flag] if f)
        # C++ flags — force modern std + preemptive includes for GCC 13 compat
        cxx_flags = " ".join(
            f for f in [debug_flag, ndebug_flag, wno_error, "-std=gnu++17",
                        "-include cstdint", "-include cstddef", "-include climits",
                        "-include limits",
                        # Disable warnings (not just -Werror) — projects may set
                        # their own -Werror AFTER our flags so -Wno-error= won't stick.
                        "-Wno-implicit-int-float-conversion",
                        "-Wno-vla-cxx-extension",
                        "-Wno-deprecated-declarations",
                        compiler_flag] if f)
        all_flags = c_flags  # for autotools/make (mostly C)
        bt_upper = cmake_build_type.upper()
        extra_flags = f'CFLAGS="$CFLAGS {c_flags}" CXXFLAGS="$CXXFLAGS {cxx_flags}"'

        # Per-package cmake overrides (e.g. disable benchmarks for ada)
        pkg_overrides = ''
        if self._package_name and self._package_name in _PACKAGE_CMAKE_OVERRIDES:
            pkg_overrides = _PACKAGE_CMAKE_OVERRIDES[self._package_name]

        cmake_flags = (
            f'-DCMAKE_BUILD_TYPE="{cmake_build_type}" '
            f'-DCMAKE_C_FLAGS="{c_flags}" '
            f'-DCMAKE_CXX_FLAGS="{cxx_flags}" '
            f'-DCMAKE_C_FLAGS_{bt_upper}="{c_flags}" '
            f'-DCMAKE_CXX_FLAGS_{bt_upper}="{cxx_flags}" '
            f'-DBUILD_SHARED_LIBS=ON {pkg_overrides}'
        )

        # Export CFLAGS/CXXFLAGS as env vars so ExternalProject/subbuilds
        # also pick up -Wno-error (cmake -D flags don't propagate)
        env_prefix = (f'export CFLAGS="$CFLAGS {c_flags}" '
                      f'CXXFLAGS="$CXXFLAGS {cxx_flags}" '
                      f'CC={self.compiler} CXX={self.compiler.replace("gcc","g++").replace("clang","clang++")} && ')

        # If configure.ac exists but configure doesn't, run autoreconf first
        has_configure = os.path.isfile(os.path.join(clone_dir, 'configure'))
        has_configure_ac = (os.path.isfile(os.path.join(clone_dir, 'configure.ac'))
                            or os.path.isfile(os.path.join(clone_dir, 'configure.in')))
        has_autogen = (os.path.isfile(os.path.join(clone_dir, 'autogen.sh'))
                       or os.path.isfile(os.path.join(clone_dir, 'bootstrap.sh')))
        if has_configure_ac and not has_configure:
            # autopoint installs gettext infrastructure (config.rpath etc.)
            # Some projects (flac) need it before autoreconf.
            if has_autogen:
                autoreconf = '(autopoint -f 2>/dev/null || true) && ./autogen.sh 2>/dev/null || autoreconf -fi && '
            else:
                autoreconf = '(autopoint -f 2>/dev/null || true) && autoreconf -fi && '
        else:
            autoreconf = ''

        # Check for configure.py packages (e.g. botan)
        has_configure_py = (self._package_name in _CONFIGURE_PY_PACKAGES
                            and os.path.isfile(os.path.join(clone_dir, 'configure.py')))

        # Check for header-only packages
        if self._package_name in _HEADER_ONLY_PACKAGES:
            header, define, extra = _HEADER_ONLY_PACKAGES[self._package_name]
            header_path = os.path.join(clone_dir, header)
            if os.path.isfile(header_path):
                impl_c = os.path.join(clone_dir, '_impl.c')
                with open(impl_c, 'w') as f:
                    f.write(f'#define {define}\n#include "{header}"\n')
                lib_name = f'lib{self._package_name.replace("-","_")}'
                cmd = (f'cd {clone_dir} && {env_prefix}'
                       f'$CC -c -fPIC {c_flags} _impl.c -o _impl.o && '
                       f'ar rcs {lib_name}.a _impl.o && '
                       f'$CC -shared -o {lib_name}.so _impl.o')
                logger.debug("Header-only build cmd: %s", cmd)
                out, err, exit_code = self.cmd_with_output(cmd, 120)
                return_code = (BuildStatus.SUCCESS if exit_code == 0
                               else BuildStatus.FAILED)
                self.own_dir(os.path.dirname(clone_dir))
                return out.decode() + err.decode(), return_code

        # Special case: lua — github.com/lua/lua mirror has source files at
        # the REPO ROOT with no Makefile. Compile all *.c except interpreter
        # mains (lua.c, luac.c) into liblua.a/.so.
        if self._package_name == 'lua':
            root_c = [f for f in os.listdir(clone_dir)
                      if f.endswith('.c') and f not in ('lua.c', 'luac.c')]
            if root_c and not os.path.isfile(os.path.join(clone_dir, 'Makefile')):
                src_list = ' '.join(sorted(root_c))
                cmd = (f'cd {clone_dir} && {env_prefix}'
                       f'$CC -c -fPIC -DLUA_USE_LINUX {c_flags} {src_list} && '
                       f'ar rcs liblua.a *.o && '
                       f'$CC -shared -o liblua.so *.o -ldl -lm')
                out, err, exit_code = self.cmd_with_output(cmd, 300)
                return_code = (BuildStatus.SUCCESS if exit_code == 0
                               else BuildStatus.FAILED)
                self.own_dir(os.path.dirname(clone_dir))
                return out.decode() + err.decode(), return_code

        # Special case: implot needs imgui headers.
        # Pin to imgui v1.87 — old enough to retain ImGuiKeyModFlags for implot v0.11-v0.13
        # while still having IM_OFFSETOF for later versions.
        if self._package_name == 'implot':
            imgui_dir = os.path.join(os.path.dirname(clone_dir), '_imgui_headers')
            if not os.path.isdir(imgui_dir):
                self.cmd_with_output(
                    f'git clone --branch v1.87 --depth=1 https://github.com/ocornut/imgui {imgui_dir}',
                    120)
            sources = ['implot.cpp', 'implot_items.cpp']
            existing = [s for s in sources if os.path.isfile(os.path.join(clone_dir, s))]
            if existing:
                src_list = ' '.join(existing)
                # -DIMGUI_DEFINE_MATH_OPERATORS required for implot internal usage
                cmd = (f'cd {clone_dir} && {env_prefix}'
                       f'$CXX -c -fPIC -DIMGUI_DEFINE_MATH_OPERATORS {cxx_flags} '
                       f'-I. -I{imgui_dir} {src_list} && '
                       f'ar rcs libimplot.a *.o && '
                       f'$CXX -shared -o libimplot.so *.o')
                out, err, exit_code = self.cmd_with_output(cmd, 300)
                return_code = (BuildStatus.SUCCESS if exit_code == 0
                               else BuildStatus.FAILED)
                self.own_dir(os.path.dirname(clone_dir))
                return out.decode() + err.decode(), return_code

        # Check for raw .cpp/.c packages (no build system)
        if self._package_name in _RAW_CPP_PACKAGES:
            sources = _RAW_CPP_PACKAGES[self._package_name]
            existing = [s for s in sources if os.path.isfile(os.path.join(clone_dir, s))]
            if existing:
                lib_name = f'lib{self._package_name.replace("-","_")}'
                src_list = ' '.join(existing)
                # Detect C vs C++ from extensions
                is_cpp = any(s.endswith(('.cpp', '.cc', '.cxx')) for s in existing)
                comp = '$CXX' if is_cpp else '$CC'
                flags = cxx_flags if is_cpp else c_flags
                inc_dirs = '-I. -Ilib -Isrc -Icpp -Iinclude'
                cmd = (f'cd {clone_dir} && {env_prefix}'
                       f'{comp} -c -fPIC {flags} {inc_dirs} {src_list} && '
                       f'ar rcs {lib_name}.a *.o && '
                       f'{comp} -shared -o {lib_name}.so *.o')
                logger.debug("Raw source build cmd: %s", cmd)
                out, err, exit_code = self.cmd_with_output(cmd, 300)
                return_code = (BuildStatus.SUCCESS if exit_code == 0
                               else BuildStatus.FAILED)
                self.own_dir(os.path.dirname(clone_dir))
                return out.decode() + err.decode(), return_code

        cmd = ""
        if has_configure_py:
            cmd = (f'cd {clone_dir} && {env_prefix}'
                   f'python3 configure.py --cc={self.compiler} '
                   f'--cxxflags="{all_flags}" && '
                   f'make -j{self.num_p_job}')
        elif 'bootstrap' in build_tool:
            # boost uses bootstrap.sh + b2 (no configure)
            if self._package_name == 'boost':
                cmd = (f'cd {clone_dir} && {env_prefix}'
                       f'bash ./bootstrap.sh --with-toolset=$([ "${{CC##*/}}" = clang ] && echo clang || echo gcc) && '
                       f'./b2 headers && '
                       f'./b2 link=shared,static variant=release threading=multi -j{self.num_p_job} '
                       f'cflags="{c_flags}" cxxflags="{cxx_flags}" 2>&1 | tail -120 || true')
            else:
                # Try bootstrap.sh or bootstrap, then configure + make
                boot_script = './bootstrap.sh' if os.path.isfile(
                    os.path.join(clone_dir, 'bootstrap.sh')) else './bootstrap'
                cmd = (f'cd {clone_dir} && {env_prefix}{autoreconf}'
                       f'bash {boot_script} && '
                       f'bash ./configure && '
                       f'make {extra_flags} -j{self.num_p_job}')
        elif build_tool in ('configure', 'autoconf'):
            # libpq: PostgreSQL - only build src/interfaces/libpq (client lib)
            if self._package_name == 'libpq':
                # Dependency order: port + common must be built before libpq.
                # Don't pass CFLAGS via make args — postgres sets per-file target
                # flags (-msse4.2 for pg_crc32c_sse42.c) that conflict with our
                # global -march; let the build's own flags win.
                cmd = (f'cd {clone_dir} && '
                       f'export CC={self.compiler} CXX={self.compiler.replace("gcc","g++").replace("clang","clang++")} && '
                       f'bash ./configure --without-readline --without-zlib --without-icu && '
                       f'make -C src/port -j{self.num_p_job} && '
                       f'make -C src/common -j{self.num_p_job} && '
                       f'make -C src/interfaces/libpq -j{self.num_p_job}')
            # lua: plain Makefile-based, need make linux
            elif self._package_name == 'lua':
                cmd = (f'cd {clone_dir} && {env_prefix}'
                       f'make linux CC="$CC" {extra_flags} -j{self.num_p_job} && '
                       f'cd src && ar rcs liblua.a lapi.o lcode.o lctype.o ldebug.o ldo.o ldump.o '
                       f'lfunc.o lgc.o llex.o lmem.o lobject.o lopcodes.o lparser.o lstate.o '
                       f'lstring.o ltable.o ltm.o lundump.o lvm.o lzio.o '
                       f'lauxlib.o lbaselib.o lbitlib.o lcorolib.o ldblib.o liolib.o '
                       f'lmathlib.o loslib.o lstrlib.o ltablib.o lutf8lib.o loadlib.o linit.o '
                       f'2>/dev/null || true')
            # tcl: configure is in unix/ subdir on tcltk/tcl mirror
            elif (self._package_name == 'tcl'
                  and os.path.isfile(os.path.join(clone_dir, 'unix', 'configure'))):
                cmd = (f'cd {clone_dir}/unix && {env_prefix}'
                       f'bash ./configure --enable-shared && '
                       f'make {extra_flags} -j{self.num_p_job}')
            # sqlite3: need --enable-shared to produce libsqlite3.so
            elif self._package_name == 'sqlite3':
                cmd = (f'cd {clone_dir} && {env_prefix}{autoreconf}'
                       f'bash ./configure --enable-shared --disable-tcl && '
                       f'make {extra_flags} -j{self.num_p_job}')
            else:
                cmd = (f'cd {clone_dir} && {env_prefix}{autoreconf}'
                       f'bash ./configure && '
                       f'make {extra_flags} -j{self.num_p_job}')
        elif 'cmake' in build_tool:
            src_dir = self._find_cmake_source_dir(clone_dir)
            # Use out-of-source build dir (jasper blocks in-source builds)
            build_dir = f'/tmp/cmake-build-{os.path.basename(clone_dir)}'
            if os.path.isdir(build_dir):
                shutil.rmtree(build_dir, ignore_errors=True)
            cmd = (f'{env_prefix}cmake --compile-no-warning-as-error '
                   f'-B{build_dir} -S{src_dir} {cmake_flags} && '
                   f'cd {build_dir} && make -j{self.num_p_job}')
        elif 'meson' in build_tool:
            cmd = (f'cd {clone_dir} && {env_prefix}'
                   f'meson setup build --buildtype=release '
                   f'--default-library=both '
                   f'-Dc_args="{c_flags}" -Dcpp_args="{cxx_flags}" && '
                   f'cd build && ninja -j{self.num_p_job}')
        elif 'make' in build_tool:
            # ags: Makefile is in Engine/ subdir, not root. Need SDL_sound
            # (installed in /usr/local), openal (apt), and our apeg stub.
            if self._package_name == 'ags':
                cmd = (f'cd {clone_dir}/Engine && '
                       f'export CPATH=/usr/local/include/SDL2:/usr/local/include:$CPATH '
                       f'LIBRARY_PATH=/usr/local/lib:$LIBRARY_PATH '
                       f'CC={self.compiler} CXX={self.compiler.replace("gcc","g++").replace("clang","clang++")} '
                       f'LIBS="-lopenal -lSDL2_sound" && '
                       f'make {extra_flags} LIBS="-lopenal -lSDL2_sound" -j{self.num_p_job} 2>&1 | tail -80 || true')
                logger.debug("Build cmd: %s", cmd)
                out, err, exit_code = self.cmd_with_output(cmd, 1500)
                return_code = (BuildStatus.SUCCESS if exit_code == 0
                               else BuildStatus.FAILED)
                self.own_dir(os.path.dirname(clone_dir))
                return out.decode() + err.decode(), return_code
            # If Makefile.am exists but no Makefile, need autoreconf first
            has_makefile = os.path.isfile(os.path.join(clone_dir, 'Makefile')) or \
                           os.path.isfile(os.path.join(clone_dir, 'makefile')) or \
                           os.path.isfile(os.path.join(clone_dir, 'GNUmakefile'))
            if not has_makefile and has_configure_ac:
                cmd = (f'cd {clone_dir} && {env_prefix}{autoreconf}'
                       f'bash ./configure && '
                       f'make {extra_flags} -j{self.num_p_job}')
            elif not has_makefile:
                logger.warning("No actual Makefile in %s (only Makefile.am/in)",
                               clone_dir)
                return "No Makefile found", BuildStatus.FAILED
            else:
                # lua: plain Makefile with clang-3.8 hardcoded; force CC and build lib
                if self._package_name == 'lua':
                    cmd = (f'cd {clone_dir} && {env_prefix}'
                           f'make linux CC="$CC" MYCFLAGS="{c_flags}" -j{self.num_p_job} && '
                           f'cd src && ar rcs liblua.a lapi.o lcode.o lctype.o ldebug.o ldo.o ldump.o '
                           f'lfunc.o lgc.o llex.o lmem.o lobject.o lopcodes.o lparser.o lstate.o '
                           f'lstring.o ltable.o ltm.o lundump.o lvm.o lzio.o '
                           f'lauxlib.o lbaselib.o lbitlib.o lcorolib.o ldblib.o liolib.o '
                           f'lmathlib.o loslib.o lstrlib.o ltablib.o lutf8lib.o loadlib.o linit.o '
                           f'2>/dev/null || true')
                else:
                    cmd = (f'cd {clone_dir} && {env_prefix}'
                           f'make {extra_flags} -j{self.num_p_job}')

        if not cmd:
            logger.warning("No build command for %s (build_tool=%s)",
                           repo, build_tool)
            return "No Build Command Made", BuildStatus.FAILED

        logger.debug("Build cmd: %s", cmd)
        out, err, exit_code = self.cmd_with_output(cmd, 1500)
        return_code = (BuildStatus.SUCCESS if exit_code == 0
                       else BuildStatus.FAILED)
        self.own_dir(os.path.dirname(clone_dir))
        return out.decode() + err.decode(), return_code


# ── helpers ───────────────────────────────────────────────────────────

def load_packages():
    with open(os.path.join(DATA_DIR, 'packages.json')) as f:
        return json.load(f)


def progress_key(pkg):
    return f"{pkg['package_name']}@{pkg['version']}@{pkg['commit_hash']}"


def load_progress(path):
    """Load progress, merging any concurrent updates."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'completed': [], 'failed': []}


def save_progress(path, progress):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f'.tmp.{os.getpid()}'
    with open(tmp, 'w') as f:
        json.dump(progress, f)
    os.replace(tmp, path)


def try_claim(claim_dir, key):
    """Try to claim a package for building. Returns True if we got the claim.

    Uses mkdir atomicity: only one process can create a given directory.
    """
    claim_path = os.path.join(claim_dir, key.replace('/', '_'))
    try:
        os.mkdir(claim_path)
        return True
    except FileExistsError:
        return False


# ── core build logic ─────────────────────────────────────────────────

def build_one(strategy, artifacts_bucket, projects_bucket,
              pkg, compiler, compiler_flag):
    """Clone, build, upload one package.  Returns True on success."""
    url = pkg.get('github_url')
    commit = pkg['commit_hash']
    package_name = pkg['package_name']
    version = pkg['version']

    if not url:
        logger.warning("No URL for %s v%s, skipping", package_name, version)
        return False

    # Pre-clean any leftover clone dir for this repo
    username, project = strategy.parse_github_name(url)
    if username and project:
        old_dir = f'{BASE_PATH}/projects/{username}/{project}'
        if os.path.isdir(old_dir):
            shutil.rmtree(old_dir, ignore_errors=True)

    # ── Clone ──
    msg, status, clone_dir = strategy.clone_data(url)
    if status != CloneStatus.SUCCESS:
        logger.warning("Clone failed: %s v%s: %s", package_name, version, msg)
        return False

    try:
        # Determine if commit is a full SHA hash or a version tag
        is_hash = len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)

        # Checkout exact commit, with fallback for fetch + version tags
        _, err, code = strategy.cmd_with_output(
            f'git checkout {commit}', 120, clone_dir)
        if code != 0 and is_hash:
            # Commit not reachable from default branch — fetch it explicitly
            _, _, fc = strategy.cmd_with_output(
                f'git fetch origin {commit}', 120, clone_dir)
            if fc == 0:
                _, err, code = strategy.cmd_with_output(
                    f'git checkout FETCH_HEAD', 30, clone_dir)
        if code != 0:
            # Try as a version tag with common prefixes
            # Generate tag variants: v1.0.0, name-1.0.0, NAME_1_0_0, etc.
            ver_underscored = commit.replace('.', '_')
            ver_hyphenated = commit.replace('.', '-')
            tag_attempts = [
                f'v{commit}', f'tags/{commit}', f'tags/v{commit}',
                f'{package_name}-{commit}',  # cfitsio-4.0.0
                f'{package_name}-v{commit}',
                f'{package_name.upper()}_{ver_underscored}',  # CRYPTOPP_8_5_0
                f'release-{commit}',
                f'R_{ver_underscored}',  # R_2_2_10 (expat)
                f'version-{commit}',     # sqlite: version-3.49.1
                f'REL_{ver_underscored}',  # postgres: REL_15_5
                f'rel/v{commit}',        # log4cxx: rel/v1.2.0
                f'VER-{ver_hyphenated}',  # freetype: VER-2-16-3
                f'{package_name}_{ver_underscored}',  # lua: lua_5_3_5
                f'{commit}-release',     # some projects use X.Y.Z-release
                f'v{commit}-stable',     # openssl style
            ]
            # Also try repo-name-based tags (e.g. openal-soft-1.19.1)
            repo_name = url.rstrip('/').split('/')[-1].replace('.git', '')
            if repo_name != package_name:
                tag_attempts.append(f'{repo_name}-{commit}')
                tag_attempts.append(f'{repo_name}-v{commit}')
            for tag in tag_attempts:
                _, _, tc = strategy.cmd_with_output(
                    f'git checkout {tag}', 30, clone_dir)
                if tc == 0:
                    code = 0
                    break
        if code != 0:
            logger.warning("Checkout %s failed for %s: %s",
                           commit, package_name, err)
            return False

        # Verify we're on the right commit (skip for tag-based checkouts)
        if is_hash:
            out, _, code = strategy.cmd_with_output(
                'git rev-parse HEAD', 30, clone_dir)
            if code == 0:
                actual = out.decode().strip()
                if not actual.startswith(commit[:12]):
                    logger.warning("Commit mismatch: wanted %s, got %s",
                                   commit, actual)
                    return False

        # Update submodules at this commit
        strategy.cmd_with_output(
            'git submodule update --init --recursive', 300, clone_dir)

        # Pre-fetch dependencies for packages that need them
        if package_name == 'blend2d':
            asmjit_dir = os.path.join(clone_dir, '3rdparty', 'asmjit')
            # submodule init may create an empty dir; force clean-clone
            if os.path.isdir(asmjit_dir) and not os.listdir(asmjit_dir):
                shutil.rmtree(asmjit_dir, ignore_errors=True)
            if not os.path.isdir(asmjit_dir):
                strategy.cmd_with_output(
                    f'git clone --depth=1 https://github.com/asmjit/asmjit {asmjit_dir}',
                    120)

        # Patch taglib CMakeLists: old versions set CXX_STANDARD 98 per-target,
        # which overrides our -DCMAKE_CXX_STANDARD=17. Rewrite it.
        if package_name == 'taglib':
            for root, _, files in os.walk(clone_dir):
                for fn in files:
                    if fn == 'CMakeLists.txt':
                        fpath = os.path.join(root, fn)
                        try:
                            with open(fpath, 'r', errors='replace') as fh:
                                content = fh.read()
                            new = re.sub(r'CXX_STANDARD\s+(9[0-9]|1[01])\b',
                                         'CXX_STANDARD 17', content)
                            if new != content:
                                with open(fpath, 'w') as fh:
                                    fh.write(new)
                        except Exception:
                            pass

        # ags 6.1.0: This commit removed Allegro but kept apeg (MPEG decoder)
        # in Makefile-objs. Replace APEG with a stub that exports the symbols
        # referenced by video.cpp as no-ops.
        if package_name == 'ags':
            objs = os.path.join(clone_dir, 'Engine', 'Makefile-objs')
            stub_c = os.path.join(clone_dir, 'Engine', 'libsrc',
                                   'apeg-1.2.1', 'apeg_stub.c')
            os.makedirs(os.path.dirname(stub_c), exist_ok=True)
            try:
                with open(stub_c, 'w') as fh:
                    fh.write('''/* Stub for apeg — ags removed Allegro but kept apeg references */
typedef struct APEG_STREAM APEG_STREAM;
APEG_STREAM *apeg_open_stream_ex(void *ptr) { (void)ptr; return 0; }
void apeg_close_stream(APEG_STREAM *s) { (void)s; }
int apeg_ignore_audio(int i) { (void)i; return 0; }
int apeg_ignore_video(int i) { (void)i; return 0; }
void apeg_set_stream_reader(int (*init)(void*), int (*read)(void*,int,void*), void (*skip)(int,void*)) { (void)init;(void)read;(void)skip; }
void apeg_set_display_depth(int d) { (void)d; }
void apeg_disable_length_detection(int s) { (void)s; }
void apeg_get_video_size(APEG_STREAM *s, int *w, int *h) { (void)s;if(w)*w=0;if(h)*h=0; }
void apeg_set_audio_callbacks(int (*a)(APEG_STREAM*,int*,int*,void*), int (*b)(APEG_STREAM*,void*,int,void*), void *p) { (void)a;(void)b;(void)p; }
void apeg_set_display_callbacks(int (*a)(APEG_STREAM*,int,int,void*), void (*b)(APEG_STREAM*,unsigned char**,void*), void *p) { (void)a;(void)b;(void)p; }
void apeg_set_error(APEG_STREAM *s, const char *t) { (void)s;(void)t; }
int apeg_get_audio_frame(APEG_STREAM *s, unsigned char **b, int *c) { (void)s;(void)b;(void)c; return 0; }
int apeg_get_video_frame(APEG_STREAM *s) { (void)s; return 0; }
int apeg_display_video_frame(APEG_STREAM *s) { (void)s; return 0; }
int apeg_reset_stream(APEG_STREAM *s) { (void)s; return 0; }
void apeg_reset_colors(APEG_STREAM *s) { (void)s; }
''')
            except Exception:
                pass
            if os.path.isfile(objs):
                try:
                    with open(objs, 'r') as fh:
                        content = fh.read()
                    # Replace APEG with just the stub file
                    new = re.sub(r'^APEG\s*=.*$',
                                 'APEG = libsrc/apeg-1.2.1/apeg_stub.c',
                                 content, flags=re.MULTILINE)
                    new = re.sub(r'^MOJOAL\s*=.*$', 'MOJOAL =',
                                 new, flags=re.MULTILINE)
                    if new != content:
                        with open(objs, 'w') as fh:
                            fh.write(new)
                except Exception:
                    pass
        # ags 6.1.0: Engine/CMakeLists.txt has broken refs to 'common' target
        # and missing include paths to Common/. Strip the bad line and inject
        # include_directories for Common/ subdirs.
        if package_name == 'ags':
            cm = os.path.join(clone_dir, 'Engine', 'CMakeLists.txt')
            if os.path.isfile(cm):
                try:
                    with open(cm, 'r') as fh:
                        content = fh.read()
                    new = re.sub(r'^(target_link_libraries\s*\(\s*common\s+)',
                                 r'# stripped: \1', content, flags=re.MULTILINE)
                    # Inject include dirs at the top to fix missing Common/ headers
                    prelude = (
                        'include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../Common)\n'
                        'include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../Common/ac)\n'
                        'include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../Common/ac/dynobj)\n'
                        'include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../Common/gui)\n'
                        'include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../Common/core)\n')
                    if 'include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../Common)' not in new:
                        new = prelude + new
                    if new != content:
                        with open(cm, 'w') as fh:
                            fh.write(new)
                except Exception:
                    pass

        # jsoncpp/vvenc: strip -Werror from add_compile_options / target_compile_options
        # across ALL CMakeLists.txt in the project (subprojects may bypass root).
        if package_name in ('jsoncpp', 'vvenc'):
            patt = re.compile(
                r'(?:add|target)_compile_options\s*\([^)]*-Werror[^)]*\)',
                re.IGNORECASE)
            for root, dirs, files in os.walk(clone_dir):
                dirs[:] = [d for d in dirs if d != '.git']
                for fn in files:
                    if fn == 'CMakeLists.txt':
                        fpath = os.path.join(root, fn)
                        try:
                            with open(fpath, 'r') as fh:
                                content = fh.read()
                            new = patt.sub('# stripped -Werror', content)
                            if new != content:
                                with open(fpath, 'w') as fh:
                                    fh.write(new)
                        except Exception:
                            pass

        # openal 1.21.0: std::aligned_alloc no longer available in newer clang+glibc;
        # patch the source to use bare aligned_alloc.
        if package_name == 'openal':
            almalloc = os.path.join(clone_dir, 'common', 'almalloc.cpp')
            if os.path.isfile(almalloc):
                try:
                    with open(almalloc, 'r') as fh:
                        content = fh.read()
                    new = content.replace('std::aligned_alloc', '::aligned_alloc')
                    if new != content:
                        with open(almalloc, 'w') as fh:
                            fh.write(new)
                except Exception:
                    pass

        # CppCommon old commits reference cmake modules (SystemInformation,
        # SetCompilerFeatures, ...) that are missing from the git tree.
        # Comment out those include() lines so cmake can proceed.
        if package_name == 'cppcommon':
            cm = os.path.join(clone_dir, 'CMakeLists.txt')
            if os.path.isfile(cm):
                try:
                    with open(cm, 'r', errors='replace') as fh:
                        content = fh.read()
                    # Comment out any include(...) that references a missing
                    # module file in cmake/
                    cmake_dir = os.path.join(clone_dir, 'cmake')
                    def _comment_missing(m):
                        name = m.group(1)
                        if not os.path.isfile(
                                os.path.join(cmake_dir, f'{name}.cmake')):
                            return f'# skipped-missing: include({name})'
                        return m.group(0)
                    new = re.sub(r'include\(([A-Za-z0-9_]+)\)',
                                 _comment_missing, content)
                    if new != content:
                        with open(cm, 'w') as fh:
                            fh.write(new)
                except Exception:
                    pass

        # Snapshot original files (to filter out pre-existing binaries)
        original_files = list(glob.iglob(clone_dir + '**/**', recursive=True))

        # ── Build ──
        build_msg, build_status = strategy.run_build(
            repo=url, clone_dir=clone_dir, compiler_flag=compiler_flag)

        if build_status != BuildStatus.SUCCESS:
            logger.info("Build exit non-zero: %s v%s (checking for partial binaries)",
                        package_name, version)
            msg_str = build_msg if isinstance(build_msg, str) else (
                build_msg.decode('utf-8', errors='replace') if isinstance(build_msg, bytes) else str(build_msg))
            lines = msg_str.splitlines()
            # Find lines matching error keywords; grab ±3 lines of context
            err_re = re.compile(r'error:|Error|CMake Error|fatal|undefined reference|cannot open|No such file', re.IGNORECASE)
            err_idx = [i for i, l in enumerate(lines) if err_re.search(l)]
            if err_idx:
                shown, blocks = set(), []
                for i in err_idx[:10]:
                    for j in range(max(0, i - 2), min(len(lines), i + 4)):
                        if j not in shown:
                            blocks.append(lines[j]); shown.add(j)
                    blocks.append('--')
                tail = '\n'.join(blocks)
            else:
                tail = '\n'.join(lines[-60:])
            logger.warning("Build errors for %s v%s:\n%s", package_name, version, tail)

        # ── Collect binaries (filter cmake internal test files) ──
        # Check even on build failure — many projects produce libs but fail on tests
        # Search both clone_dir and out-of-source cmake build dir
        search_dirs = [clone_dir]
        cmake_build = f'/tmp/cmake-build-{os.path.basename(clone_dir)}'
        if os.path.isdir(cmake_build):
            search_dirs.append(cmake_build)
        bin_found = set()
        for search_dir in search_dirs:
            bin_found |= {
                f for f in strategy.find_binaries(search_dir)
                if os.path.exists(f) and f not in original_files
                and '/CMakeFiles/' not in f
            }

        # ── DWARF extraction (only on real binaries) ──
        dwarf_list = []
        for binfile in bin_found:
            try:
                item = strategy._extract_dwarf_info(binfile)
                if item:
                    dwarf_list.append(item)
            except Exception as e:
                logger.warning("DWARF failed for %s: %s", binfile, e)
        if not bin_found:
            logger.warning("No binaries: %s v%s", package_name, version)
            return False

        logger.info("Found %d binaries for %s v%s",
                     len(bin_found), package_name, version)

        # ── Upload to MinIO ──
        prefix = f"{username}_{project}_{commit}_{compiler}_{compiler_flag}"

        for fpath in bin_found:
            s3_key = f"{prefix}/{os.path.basename(fpath)}"
            artifacts_bucket.upload_file(fpath, s3_key)

        # Metadata
        metadata = {
            'Platform': 'linux',
            'Build_mode': 'RelWithDebInfo',
            'Compiler': compiler,
            'Compiler_version': strategy.compiler_version,
            'URL': url,
            'Commit': commit,
            'Optimization': compiler_flag,
            'compiler_flag': compiler_flag,
            'language': 'c++',
            'library': 'x64',
            'package_name': package_name,
            'version': version,
            'license': pkg.get('license', ''),
        }
        if dwarf_list:
            metadata['Binary_info_list'] = dwarf_list

        meta_path = os.path.join(clone_dir, 'assemblage_meta.json')
        with open(meta_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        artifacts_bucket.upload_file(meta_path, f"{prefix}/assemblage_meta.json")

        # Source archive (idempotent -- same content regardless of opt flag)
        archive = shutil.make_archive(f'/tmp/{commit}', 'gztar', clone_dir)
        projects_bucket.upload_file(
            archive, f"{username}/{project}/{commit}.tar.gz")
        try:
            os.remove(archive)
        except OSError:
            pass

        return True

    finally:
        parent = os.path.dirname(clone_dir)
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except Exception:
            pass


# ── main loop ─────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        stream=sys.stdout,
    )

    compiler = os.environ.get('COMPILER', 'gcc')
    compiler_flag = os.environ.get('COMPILER_FLAG', '-O2')

    s3_host = os.environ.get('S3_HOST', 'minio')
    s3_port = int(os.environ.get('S3_PORT', '9000'))
    s3_access = os.environ.get('S3_ACCESS_KEY', 'minioadmin')
    s3_secret = os.environ.get('S3_SECRET_ACCESS_KEY', 'minioadmin')
    s3_https = os.environ.get('S3_HTTPS', 'false').lower() == 'true'

    logger.info("DeepHistory Linux Builder: %s %s", compiler, compiler_flag)

    os.makedirs(f'{BASE_PATH}/projects', exist_ok=True)

    # Lower parallelism: 128 host cores / 24 workers = ~5 cores each to avoid thrashing
    strategy = DeepHistoryBuildStrategy(
        compiler=compiler, language='c++', library='x64',
        save_assembly=False, base_path=BASE_PATH, num_p_job=4,
    )

    s3 = S3Client(host=s3_host, port=s3_port,
                   access_key=s3_access, secret_access_key=s3_secret,
                   https=s3_https)
    artifacts = S3Bucket(s3, 'deephistory-artifacts')
    projects = S3Bucket(s3, 'deephistory-sources')

    packages = load_packages()
    flag_slug = compiler_flag.replace('-', '')
    progress_file = os.path.join(DATA_DIR, 'progress',
                                  f'{compiler}_{flag_slug}.json')
    claim_dir = os.path.join(DATA_DIR, 'claims', f'{compiler}_{flag_slug}')
    os.makedirs(claim_dir, exist_ok=True)

    total = len(packages)
    successes = 0
    failures = 0
    skipped = 0

    for i, pkg in enumerate(packages):
        key = progress_key(pkg)

        # Re-read progress each iteration (other replicas update it)
        progress = load_progress(progress_file)
        done_set = set(progress['completed'] + progress['failed'])
        if key in done_set:
            skipped += 1
            continue

        # Try to claim this package (atomic mkdir)
        if not try_claim(claim_dir, key):
            skipped += 1
            continue

        t0 = time.time()
        logger.info("[%d/%d] %s v%s", i + 1, total,
                    pkg['package_name'], pkg['version'])

        strategy._package_name = pkg['package_name']
        ok = build_one(strategy, artifacts, projects,
                       pkg, compiler, compiler_flag)

        elapsed = time.time() - t0

        # Merge result into progress (re-read to avoid overwriting others)
        progress = load_progress(progress_file)
        if ok:
            progress['completed'].append(key)
            successes += 1
        else:
            progress['failed'].append(key)
            failures += 1
        save_progress(progress_file, progress)

        logger.info("%s %s v%s (%.0fs) [%d ok / %d fail]",
                    "OK " if ok else "FAIL",
                    pkg['package_name'], pkg['version'],
                    elapsed, successes, failures)

    logger.info("Done. %d succeeded, %d failed, %d skipped out of %d",
                successes, failures, skipped, total)


if __name__ == '__main__':
    main()
