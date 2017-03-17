#!/bin/bash

APP_DIR=$1

bus_id=$RANDOM
sp_id=$RANDOM

gid=$(./taas -H localhost:5061 run --memsize 50 --name bus_${bus_id}_sp_${sp_id} tarantool)
if [ $? -eq 0 ]; then
    gid=$(echo $gid | tail -n 1 | grep -Eo "[0-9a-zA-A]+")
else
    echo $gid
    exit 1
fi

echo "group_id=$gid"

./taas -v -H localhost:5061 deploy $gid $APP_DIR

curl -X POST localhost:8081/business/${bus_id}/sp/${sp_id}/products -d '{"name":"TEST","vendor_code":"test"}'