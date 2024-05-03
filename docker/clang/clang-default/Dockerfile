FROM assemblage-clang:base

ENV CC='clang'
ENV CXX='clang++'

RUN echo '2:clang' >> /conf

RUN cp /usr/bin/clang /usr/bin/cc && cp /usr/bin/clang++ /usr/bin/cc1plus && \
    cp -f /usr/bin/clang /usr/bin/gcc && cp -f /usr/bin/clang++ /usr/bin/g++

WORKDIR /assemblage

