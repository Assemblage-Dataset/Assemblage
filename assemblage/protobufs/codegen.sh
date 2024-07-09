# gen rpc file

python -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. --experimental_allow_proto3_optional  ./assemblage.proto
