docker rm -f `docker ps | grep cloud-tarantool | awk '{print $1}'`
docker-compose down --rmi all
docker-compose up
