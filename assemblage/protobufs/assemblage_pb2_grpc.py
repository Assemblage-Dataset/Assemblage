# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc

from assemblage.protobufs import assemblage_pb2 as assemblage__pb2


class AssemblageServiceStub(object):
    """Missing associated documentation comment in .proto file."""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.queryRepo = channel.unary_stream(
            '/AssemblageService/queryRepo',
            request_serializer=assemblage__pb2.RepoRequest.SerializeToString,
            response_deserializer=assemblage__pb2.Repo.FromString,
        )
        self.failedRepo = channel.unary_stream(
            '/AssemblageService/failedRepo',
            request_serializer=assemblage__pb2.RepoRequest.SerializeToString,
            response_deserializer=assemblage__pb2.Repo.FromString,
        )
        self.clonedFailedRepo = channel.unary_stream(
            '/AssemblageService/clonedFailedRepo',
            request_serializer=assemblage__pb2.RepoRequest.SerializeToString,
            response_deserializer=assemblage__pb2.Repo.FromString,
        )
        self.dumpSuccessRepo = channel.unary_stream(
            '/AssemblageService/dumpSuccessRepo',
            request_serializer=assemblage__pb2.DumpRequest.SerializeToString,
            response_deserializer=assemblage__pb2.Repo.FromString,
        )
        self.dumpSuccessStatus = channel.unary_stream(
            '/AssemblageService/dumpSuccessStatus',
            request_serializer=assemblage__pb2.DumpRequest.SerializeToString,
            response_deserializer=assemblage__pb2.BStatus.FromString,
        )
        self.workerStatus = channel.unary_stream(
            '/AssemblageService/workerStatus',
            request_serializer=assemblage__pb2.WorkerRequest.SerializeToString,
            response_deserializer=assemblage__pb2.Worker.FromString,
        )
        self.buildRepo = channel.unary_unary(
            '/AssemblageService/buildRepo',
            request_serializer=assemblage__pb2.BuildRequest.SerializeToString,
            response_deserializer=assemblage__pb2.BuildResponse.FromString,
        )
        self.addBuildCmd = channel.unary_unary(
            '/AssemblageService/addBuildCmd',
            request_serializer=assemblage__pb2.CmdRequest.SerializeToString,
            response_deserializer=assemblage__pb2.CmdResponse.FromString,
        )
        self.buildInfo = channel.unary_unary(
            '/AssemblageService/buildInfo',
            request_serializer=assemblage__pb2.BuildInfoRequest.SerializeToString,
            response_deserializer=assemblage__pb2.BuildInfoResponse.FromString,
        )
        self.registWorker = channel.unary_unary(
            '/AssemblageService/registWorker',
            request_serializer=assemblage__pb2.RegisterRequest.SerializeToString,
            response_deserializer=assemblage__pb2.RegisterResponse.FromString,
        )
        self.queryRepoInfo = channel.unary_unary(
            '/AssemblageService/queryRepoInfo',
            request_serializer=assemblage__pb2.RepoRequest.SerializeToString,
            response_deserializer=assemblage__pb2.RepoInfoResponse.FromString,
        )
        self.sendBinary = channel.stream_unary(
            '/AssemblageService/sendBinary',
            request_serializer=assemblage__pb2.BinaryChunk.SerializeToString,
            response_deserializer=assemblage__pb2.BinaryResponse.FromString,
        )
        self.addBuildOpt = channel.unary_unary(
            '/AssemblageService/addBuildOpt',
            request_serializer=assemblage__pb2.BuildOpt.SerializeToString,
            response_deserializer=assemblage__pb2.CmdResponse.FromString,
        )
        self.checkProgress = channel.unary_unary(
            '/AssemblageService/checkProgress',
            request_serializer=assemblage__pb2.ProgressRequest.SerializeToString,
            response_deserializer=assemblage__pb2.ProgressResponse.FromString,
        )
        self.enableBuildOpt = channel.unary_unary(
            '/AssemblageService/enableBuildOpt',
            request_serializer=assemblage__pb2.enableBuildOptRequest.SerializeToString,
            response_deserializer=assemblage__pb2.enableBuildOptResponse.FromString,
        )
        self.getBuildOpt = channel.unary_stream(
            '/AssemblageService/getBuildOpt',
            request_serializer=assemblage__pb2.getBuildOptRequest.SerializeToString,
            response_deserializer=assemblage__pb2.BuildOpt.FromString,
        )
        self.ping = channel.unary_unary(
            '/AssemblageService/ping',
            request_serializer=assemblage__pb2.PingRequest.SerializeToString,
            response_deserializer=assemblage__pb2.PongResponse.FromString,
        )
        self.setWorkerOpt = channel.unary_unary(
            '/AssemblageService/setWorkerOpt',
            request_serializer=assemblage__pb2.SetOptRequest.SerializeToString,
            response_deserializer=assemblage__pb2.SetOptResponse.FromString,
        )


class AssemblageServiceServicer(object):
    """Missing associated documentation comment in .proto file."""

    def queryRepo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def failedRepo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def clonedFailedRepo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def dumpSuccessRepo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def dumpSuccessStatus(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def workerStatus(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def buildRepo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def addBuildCmd(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def buildInfo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def registWorker(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def queryRepoInfo(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def sendBinary(self, request_iterator, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def addBuildOpt(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def checkProgress(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def enableBuildOpt(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def getBuildOpt(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ping(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def setWorkerOpt(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_AssemblageServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
        'queryRepo': grpc.unary_stream_rpc_method_handler(
            servicer.queryRepo,
            request_deserializer=assemblage__pb2.RepoRequest.FromString,
            response_serializer=assemblage__pb2.Repo.SerializeToString,
        ),
        'failedRepo': grpc.unary_stream_rpc_method_handler(
            servicer.failedRepo,
            request_deserializer=assemblage__pb2.RepoRequest.FromString,
            response_serializer=assemblage__pb2.Repo.SerializeToString,
        ),
        'clonedFailedRepo': grpc.unary_stream_rpc_method_handler(
            servicer.clonedFailedRepo,
            request_deserializer=assemblage__pb2.RepoRequest.FromString,
            response_serializer=assemblage__pb2.Repo.SerializeToString,
        ),
        'dumpSuccessRepo': grpc.unary_stream_rpc_method_handler(
            servicer.dumpSuccessRepo,
            request_deserializer=assemblage__pb2.DumpRequest.FromString,
            response_serializer=assemblage__pb2.Repo.SerializeToString,
        ),
        'dumpSuccessStatus': grpc.unary_stream_rpc_method_handler(
            servicer.dumpSuccessStatus,
            request_deserializer=assemblage__pb2.DumpRequest.FromString,
            response_serializer=assemblage__pb2.BStatus.SerializeToString,
        ),
        'workerStatus': grpc.unary_stream_rpc_method_handler(
            servicer.workerStatus,
            request_deserializer=assemblage__pb2.WorkerRequest.FromString,
            response_serializer=assemblage__pb2.Worker.SerializeToString,
        ),
        'buildRepo': grpc.unary_unary_rpc_method_handler(
            servicer.buildRepo,
            request_deserializer=assemblage__pb2.BuildRequest.FromString,
            response_serializer=assemblage__pb2.BuildResponse.SerializeToString,
        ),
        'addBuildCmd': grpc.unary_unary_rpc_method_handler(
            servicer.addBuildCmd,
            request_deserializer=assemblage__pb2.CmdRequest.FromString,
            response_serializer=assemblage__pb2.CmdResponse.SerializeToString,
        ),
        'buildInfo': grpc.unary_unary_rpc_method_handler(
            servicer.buildInfo,
            request_deserializer=assemblage__pb2.BuildInfoRequest.FromString,
            response_serializer=assemblage__pb2.BuildInfoResponse.SerializeToString,
        ),
        'registWorker': grpc.unary_unary_rpc_method_handler(
            servicer.registWorker,
            request_deserializer=assemblage__pb2.RegisterRequest.FromString,
            response_serializer=assemblage__pb2.RegisterResponse.SerializeToString,
        ),
        'queryRepoInfo': grpc.unary_unary_rpc_method_handler(
            servicer.queryRepoInfo,
            request_deserializer=assemblage__pb2.RepoRequest.FromString,
            response_serializer=assemblage__pb2.RepoInfoResponse.SerializeToString,
        ),
        'sendBinary': grpc.stream_unary_rpc_method_handler(
            servicer.sendBinary,
            request_deserializer=assemblage__pb2.BinaryChunk.FromString,
            response_serializer=assemblage__pb2.BinaryResponse.SerializeToString,
        ),
        'addBuildOpt': grpc.unary_unary_rpc_method_handler(
            servicer.addBuildOpt,
            request_deserializer=assemblage__pb2.BuildOpt.FromString,
            response_serializer=assemblage__pb2.CmdResponse.SerializeToString,
        ),
        'checkProgress': grpc.unary_unary_rpc_method_handler(
            servicer.checkProgress,
            request_deserializer=assemblage__pb2.ProgressRequest.FromString,
            response_serializer=assemblage__pb2.ProgressResponse.SerializeToString,
        ),
        'enableBuildOpt': grpc.unary_unary_rpc_method_handler(
            servicer.enableBuildOpt,
            request_deserializer=assemblage__pb2.enableBuildOptRequest.FromString,
            response_serializer=assemblage__pb2.enableBuildOptResponse.SerializeToString,
        ),
        'getBuildOpt': grpc.unary_stream_rpc_method_handler(
            servicer.getBuildOpt,
            request_deserializer=assemblage__pb2.getBuildOptRequest.FromString,
            response_serializer=assemblage__pb2.BuildOpt.SerializeToString,
        ),
        'ping': grpc.unary_unary_rpc_method_handler(
            servicer.ping,
            request_deserializer=assemblage__pb2.PingRequest.FromString,
            response_serializer=assemblage__pb2.PongResponse.SerializeToString,
        ),
        'setWorkerOpt': grpc.unary_unary_rpc_method_handler(
            servicer.setWorkerOpt,
            request_deserializer=assemblage__pb2.SetOptRequest.FromString,
            response_serializer=assemblage__pb2.SetOptResponse.SerializeToString,
        ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
        'AssemblageService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))

 # This class is part of an EXPERIMENTAL API.


class AssemblageService(object):
    """Missing associated documentation comment in .proto file."""

    @staticmethod
    def queryRepo(request,
                  target,
                  options=(),
                  channel_credentials=None,
                  call_credentials=None,
                  insecure=False,
                  compression=None,
                  wait_for_ready=None,
                  timeout=None,
                  metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/queryRepo',
                                              assemblage__pb2.RepoRequest.SerializeToString,
                                              assemblage__pb2.Repo.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def failedRepo(request,
                   target,
                   options=(),
                   channel_credentials=None,
                   call_credentials=None,
                   insecure=False,
                   compression=None,
                   wait_for_ready=None,
                   timeout=None,
                   metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/failedRepo',
                                              assemblage__pb2.RepoRequest.SerializeToString,
                                              assemblage__pb2.Repo.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def clonedFailedRepo(request,
                         target,
                         options=(),
                         channel_credentials=None,
                         call_credentials=None,
                         insecure=False,
                         compression=None,
                         wait_for_ready=None,
                         timeout=None,
                         metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/clonedFailedRepo',
                                              assemblage__pb2.RepoRequest.SerializeToString,
                                              assemblage__pb2.Repo.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def dumpSuccessRepo(request,
                        target,
                        options=(),
                        channel_credentials=None,
                        call_credentials=None,
                        insecure=False,
                        compression=None,
                        wait_for_ready=None,
                        timeout=None,
                        metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/dumpSuccessRepo',
                                              assemblage__pb2.DumpRequest.SerializeToString,
                                              assemblage__pb2.Repo.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def dumpSuccessStatus(request,
                          target,
                          options=(),
                          channel_credentials=None,
                          call_credentials=None,
                          insecure=False,
                          compression=None,
                          wait_for_ready=None,
                          timeout=None,
                          metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/dumpSuccessStatus',
                                              assemblage__pb2.DumpRequest.SerializeToString,
                                              assemblage__pb2.BStatus.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def workerStatus(request,
                     target,
                     options=(),
                     channel_credentials=None,
                     call_credentials=None,
                     insecure=False,
                     compression=None,
                     wait_for_ready=None,
                     timeout=None,
                     metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/workerStatus',
                                              assemblage__pb2.WorkerRequest.SerializeToString,
                                              assemblage__pb2.Worker.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def buildRepo(request,
                  target,
                  options=(),
                  channel_credentials=None,
                  call_credentials=None,
                  insecure=False,
                  compression=None,
                  wait_for_ready=None,
                  timeout=None,
                  metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/buildRepo',
                                             assemblage__pb2.BuildRequest.SerializeToString,
                                             assemblage__pb2.BuildResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def addBuildCmd(request,
                    target,
                    options=(),
                    channel_credentials=None,
                    call_credentials=None,
                    insecure=False,
                    compression=None,
                    wait_for_ready=None,
                    timeout=None,
                    metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/addBuildCmd',
                                             assemblage__pb2.CmdRequest.SerializeToString,
                                             assemblage__pb2.CmdResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def buildInfo(request,
                  target,
                  options=(),
                  channel_credentials=None,
                  call_credentials=None,
                  insecure=False,
                  compression=None,
                  wait_for_ready=None,
                  timeout=None,
                  metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/buildInfo',
                                             assemblage__pb2.BuildInfoRequest.SerializeToString,
                                             assemblage__pb2.BuildInfoResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def registWorker(request,
                     target,
                     options=(),
                     channel_credentials=None,
                     call_credentials=None,
                     insecure=False,
                     compression=None,
                     wait_for_ready=None,
                     timeout=None,
                     metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/registWorker',
                                             assemblage__pb2.RegisterRequest.SerializeToString,
                                             assemblage__pb2.RegisterResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def queryRepoInfo(request,
                      target,
                      options=(),
                      channel_credentials=None,
                      call_credentials=None,
                      insecure=False,
                      compression=None,
                      wait_for_ready=None,
                      timeout=None,
                      metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/queryRepoInfo',
                                             assemblage__pb2.RepoRequest.SerializeToString,
                                             assemblage__pb2.RepoInfoResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def sendBinary(request_iterator,
                   target,
                   options=(),
                   channel_credentials=None,
                   call_credentials=None,
                   insecure=False,
                   compression=None,
                   wait_for_ready=None,
                   timeout=None,
                   metadata=None):
        return grpc.experimental.stream_unary(request_iterator, target, '/AssemblageService/sendBinary',
                                              assemblage__pb2.BinaryChunk.SerializeToString,
                                              assemblage__pb2.BinaryResponse.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def addBuildOpt(request,
                    target,
                    options=(),
                    channel_credentials=None,
                    call_credentials=None,
                    insecure=False,
                    compression=None,
                    wait_for_ready=None,
                    timeout=None,
                    metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/addBuildOpt',
                                             assemblage__pb2.BuildOpt.SerializeToString,
                                             assemblage__pb2.CmdResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def checkProgress(request,
                      target,
                      options=(),
                      channel_credentials=None,
                      call_credentials=None,
                      insecure=False,
                      compression=None,
                      wait_for_ready=None,
                      timeout=None,
                      metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/checkProgress',
                                             assemblage__pb2.ProgressRequest.SerializeToString,
                                             assemblage__pb2.ProgressResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def enableBuildOpt(request,
                       target,
                       options=(),
                       channel_credentials=None,
                       call_credentials=None,
                       insecure=False,
                       compression=None,
                       wait_for_ready=None,
                       timeout=None,
                       metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/enableBuildOpt',
                                             assemblage__pb2.enableBuildOptRequest.SerializeToString,
                                             assemblage__pb2.enableBuildOptResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def getBuildOpt(request,
                    target,
                    options=(),
                    channel_credentials=None,
                    call_credentials=None,
                    insecure=False,
                    compression=None,
                    wait_for_ready=None,
                    timeout=None,
                    metadata=None):
        return grpc.experimental.unary_stream(request, target, '/AssemblageService/getBuildOpt',
                                              assemblage__pb2.getBuildOptRequest.SerializeToString,
                                              assemblage__pb2.BuildOpt.FromString,
                                              options, channel_credentials,
                                              insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def ping(request,
             target,
             options=(),
             channel_credentials=None,
             call_credentials=None,
             insecure=False,
             compression=None,
             wait_for_ready=None,
             timeout=None,
             metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/ping',
                                             assemblage__pb2.PingRequest.SerializeToString,
                                             assemblage__pb2.PongResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def setWorkerOpt(request,
                     target,
                     options=(),
                     channel_credentials=None,
                     call_credentials=None,
                     insecure=False,
                     compression=None,
                     wait_for_ready=None,
                     timeout=None,
                     metadata=None):
        return grpc.experimental.unary_unary(request, target, '/AssemblageService/setWorkerOpt',
                                             assemblage__pb2.SetOptRequest.SerializeToString,
                                             assemblage__pb2.SetOptResponse.FromString,
                                             options, channel_credentials,
                                             insecure, call_credentials, compression, wait_for_ready, timeout, metadata)
