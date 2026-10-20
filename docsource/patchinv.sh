#!/bin/bash

sphobjinv convert plain objects.inv objects.txt
sed -i 's/ py:parameter / py:paramref /g' objects.txt
sphobjinv convert zlib objects.txt objects.inv
