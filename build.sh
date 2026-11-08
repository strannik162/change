#!/bin/bash
set -e
echo "Building DDNet TAS..."
mkdir -p build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCLIENT=ON -DSERVER=ON
cmake --build . --config Release -j$(nproc)
echo "Build done: build/DDNet"
