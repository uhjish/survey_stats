import yaml
import re
import os

import pandas as pd
import sqlalchemy as sa
# import pyarrow as pa
import fastparquet as fpq
import dask
import dask.delayed as delayed
import dask.multiprocessing
import dask.cache
# from dask.distributed import LocalCluster, Client
from multiprocessing.pool import ThreadPool
from timeit import default_timer as timer

from survey_stats import log
from survey_stats.etl.sas import process_sas_survey
from survey_stats.etl.socrata import fetch_socrata_stats
from survey_stats import serdes


logger = log.getLogger(__name__)


def load_socrata_data(yaml_f, client=None):
    cfg = None
    with open(yaml_f) as fh:
        cfg = yaml.load(fh)
    params = cfg['socrata']
    logger.bind(dataset=cfg['id'])
    logger.info('loading socrata data')
    ksoda = serdes.socrata_key4id(cfg['id'])
    dfs = [delayed(fetch_socrata_stats)(url=url,
                                        mapcols=params['mapcols'],
                                        mapvals=params['mapvals'],
                                        apply_fn=params['apply_fn'],
                                        c_filter=params['c_filter'],
                                        unstack=params['unstack'],
                                        fold_stats=params['fold_stats']
                                        ) for url in params['soda_api']]
    dfs = delayed(pd.concat)(dfs, ignore_index=True)
    dfs = dfs.compute()
    # logger.info('converting df to csv table', name=ksoda)
    # serdes.save_csv(ksoda, dfs, index=False)
    # logger.info('converting df to pyarrow table', name=ksoda)
    # tbl = pa.Table.from_pandas(dfs)
    logger.info('saving survey data to parquet', name=ksoda)
    fpq.write('cache/'+ksoda+'.parquet', data=dfs, write_index=False,
              has_nulls=True)
    logger.info('saving socrata data')
    dfs.to_feather('cache/'+ksoda+'.feather')
    return dfs


def load_survey_data(yaml_f, client=None):
    cfg = None
    with open(yaml_f) as fh:
        cfg = yaml.load(fh)
    logger.bind(dataset=cfg['id'])
    ksvy = serdes.surveys_key4id(cfg['id'])
    logger.info('loading survey dfs', sk=cfg['surveys'].keys())
    svydf = process_sas_survey(meta=cfg['surveys']['meta'],
                               prefix=cfg['surveys']['s3_url_prefix'],
                               qids=cfg['surveys']['qids'],
                               facets=cfg['facets'],
                               na_syns=cfg['surveys']['na_synonyms'],
                               repl=cfg['surveys']['replace_labels'],
                               fmts=cfg['surveys']['patch_format'],
                               client=client, lgr=logger)
    logger.info('loaded survey dfs', shape=svydf.shape)
    svydf = svydf.compute()
    svydf = svydf.reset_index(drop=True)
    logger.info('dumping to csv for bulk load',
                df=(svydf.select_dtypes(include=['object', 'category'])
                         .apply(lambda xf: xf.value_counts().to_dict())
                         .to_json(orient='index')))
    # csvf = serdes.save_csv(ksvy, svydf, index=False)
    csvt = timer()
    # logger.info('dumped to csv for bulk load', elapsed=csvt-st)
    # logger.info('converting df to pyarrow table', name=ksvy)
    # tbl = pa.Table.from_pandas(svydf)
    # logger.info('saving survey data to parquet', name=ksvy)
    # pa.write_table(tbl, 'cache/'+ksvy+'.parquet')
    fpq.write(filename='cache/'+ksvy+'_2.parquet', data=svydf,
              has_nulls=True)
    pqt = timer()
    logger.info('finally, saving survey data to feather', name=ksvy, elapsed=pqt-csvt)
    logger.info('saving survey data feather.write_dataframe', name=ksvy)
    svydf.to_feather('cache/'+ksvy+'.feather')
    logger.info('saved survey data feather.write_dataframe', name=ksvy, elapsed=timer()-pqt)
    logger.unbind('dataset')
    return svydf


def load_csv_mariadb_columnstore(df, tblname, engine):
    logger.info('creating schema for column store', name=tblname)
    start = timer()
    q = pd.io.sql.get_schema(df[:0], tblname, con=engine)
    q = q.replace('TEXT', 'VARCHAR(100)').replace('BIGINT', 'INT') + \
        ' engine=columnstore default character set=utf8;'
    q = re.sub(r'FLOAT\(\d+\)', 'FLOAT', q)
    with engine.connect() as con:
        con.execute(q)
    logger.info('bulk loaded data using cfimport', name=tblname, rows=df.shape[0], elapsed=timer()-start)


def load_csv_monetdb(df, tblname, engine):
    copy_tmpl = "COPY {nrows} OFFSET 2 RECORDS INTO {tbl} from '{csvf}'" + \
                " USING DELIMITERS ', '"
    logger.info('creating schema for column store', name=tblname)
    start = timer()
    q = pd.io.sql.get_schema(df[:0], tblname, con=engine)
    q = q.replace('\n', ' ').replace('\t', ' ')
    logger.info('dumping to csv for bulk load', q=q)
    csvf = serdes.save_csv(tblname, df, index=False)
    csvf = os.path.abspath(csvf)
    csvtime = timer()
    logger.info('bulk loading csv into monetdb', csvf=csvf, elapsed=csvtime-start)
    with engine.begin() as con:
        con.execute("DROP TABLE %s" % tblname)
        con.execute(q)
        con.execute(copy_tmpl.format(nrows=df.shape[0]+1000,
                                     tbl=tblname,
                                     csvf=csvf))
    logger.info('bulk loaded data using cfimport', name=tblname,
                rows=df.shape[0], elapsed_copy=timer()-csvtime,
                elapsed=timer()-start)


def load_sqlalchemy(df, engine, tbl):
    logger.info('loading df into monetdb table', name=tbl)
    start = timer()
    with engine.begin() as con:
        df.to_sql(tbl, con, chunksize=10000, if_exists='replace', index=False)
    logger.info('loaded dataframe into monetdb', tbl=tbl, rows=df.shape[0], elapsed_step=timer()-start)


def bulk_load_df(tblname, engine):
    logger.info('loading data from feather', name=tblname)
    df = serdes.load_feather(tblname)
    if engine.name == 'mysql':
        load_csv_mariadb_columnstore(df, tblname, engine)
    elif engine.name == 'monetdb':
        load_csv_monetdb(df, tblname, engine)


def setup_tables(yaml_f, dburl):
    cfg = None
    with open(yaml_f) as fh:
        cfg = yaml.load(fh)
    engine = sa.create_engine(dburl)
    ksvy = serdes.surveys_key4id(cfg['id'])
    bulk_load_df(ksvy, engine)
    ksoc = serdes.socrata_key4id(cfg['id'])
    bulk_load_df(ksoc, engine)


if __name__ == '__main__':
    default_sql_conn = 'mysql+pymysql://mcsuser:mcsuser@localhost:4306/survey'
    default_sql_conn = 'monetdb://monetdb:monetdb@localhost/survey'
    # lc = LocalCluster()
    # client = Client(lc)
    client = None
    # cache = dask.cache.Cache(8e9)
    # cache.register()
    dask.set_options(get=dask.threaded.get, pool=ThreadPool())
    # load_socrata_data('config/data/brfss.yaml', client)
    load_survey_data('config/data/brfss.yaml', client)
    load_socrata_data('config/data/brfss_pre2011.yaml', client)
    load_survey_data('config/data/brfss_pre2011.yaml', client)
    load_socrata_data('config/data/yrbss.yaml', client)
    load_survey_data('config/data/yrbss.yaml', client)
    '''
    setup_tables(
        'config/data/brfss.yaml',
        default_sql_conn
    )
    setup_tables(
        'config/data/brfss_pre2011.yaml',
        'mysql+pymysql://mcsuser:mcsuser@localhost:4306/survey'
    )'''
    '''
    setup_tables(
        'config/data/brfss_pre2011.yaml',
        'mysql+://mcsuser:mcsuser@localhost:4306/survey'
    )'''
