FROM postgres:17

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    postgresql-server-dev-17 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN git clone --branch v0.8.1 https://github.com/pgvector/pgvector.git && \
    cd pgvector && \
    make && \
    make install && \
    cd .. && \
    rm -rf pgvector

RUN git clone --branch v1.10.0 https://github.com/pgmq/pgmq.git && \
    cd pgmq/pgmq-extension && \
    make && \
    make install && \
    make install-pg-partman && \
    cd ../.. && \
    rm -rf pgmq


RUN apt-get remove -y build-essential git postgresql-server-dev-17 && \
    apt-get autoremove -y && \
    apt-get clean

RUN echo "shared_preload_libraries = 'pg_partman_bgw'" >> /usr/share/postgresql/postgresql.conf.sample && \
    echo "pg_partman_bgw.interval = 60" >> /usr/share/postgresql/postgresql.conf.sample && \
    echo "pg_partman_bgw.role = 'postgres'" >> /usr/share/postgresql/postgresql.conf.sample && \
    echo "pg_partman_bgw.dbname = 'postgres'" >> /usr/share/postgresql/postgresql.conf.sample

COPY init.sql /docker-entrypoint-initdb.d/init.sql

WORKDIR /
