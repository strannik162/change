@echo off
echo Building DDNet TAS on Windows MSYS2...
mkdir build
cd build
cmake .. -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DCLIENT=ON -DSERVER=ON
cmake --build . --config Release -j4
echo Build done: build/DDNet.exe
