import os
import io
import re
import us
import sys
import tempfile
import feather
import logging
import requests
import zipfile
import traceback
import dask
import dask.dataframe as dd
import dask.delayed
from dask.delayed import delayed
import numpy as np
import pandas as pd
import urllib.request
import multiprocessing
from retry import retry
from cytoolz import curry
from cytoolz.itertoolz import concat, concatv, mapcat
from cytoolz.functoolz import thread_last, thread_first, flip, do, compose
from cytoolz.curried import map, filter, reduce

US_STATES_FIPS_INTS = thread_last(
    us.STATES_AND_TERRITORIES,
    map(lambda x: x.fips),
    filter(lambda x: x is not None),
    map(lambda x: int(x)),
    list
)

WEIGHTING_COLS = {
    'weight': 'weight_col',
    'psu': 'psu_col',
    'strata': 'strata_col'
}

SAMPLING_COLS = {
    'year': 'year_col',
    'sitecode': 'sitecode_col'
}

def getLogger(name='survey_stat_deflog'):
    formatter = logging.Formatter('%(asctime)s - STATSETL: %(message)s',
                                  datefmt='%b %d %H:%M:%S')
    errlog = logging.StreamHandler()
    errlog.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(errlog)
    return logger

logger = getLogger()

def number_of_workers():
    return multiprocessing.cpu_count()-2

def parse_format_assignments(formas_f, remote_url=True):
    fh = formas_f
    if remote_url:
        r = requests.get(formas_f)
        fh = r.iter_lines(decode_unicode=True)
    format_lines = ''
    append = False
    for line in fh:
        # lowercase, trim off comments and whitespace
        l = re.split('\/?\*', line.lower())[0].strip()
        if line.strip().endswith(';'):
            # make sure we don't lose terminating semicolons
            l += ';'
        if not append and l.startswith('format'):
            # begin collecting format lines
            append = True
            format_lines += l.replace('format','',1) + ' '
            continue
        if append and l.endswith(';'):
            # stop collecting format lines
            format_lines += l.replace(';','')
            append = False
            break
        if append:
            # add format info line
            format_lines += l + ' '
            continue
    assignments = thread_last(
        format_lines.split('.'), # assignment set ends with fmt + dot
        map(lambda x: x.split()), # break out vars and format
        (mapcat, lambda y: [(k, y[-1]) for k in y]), # tuple of var, fmt
        dict
    )
    return assignments

def block2dict(lines):
    d = thread_last(
        lines,
        map(lambda x: x.strip().replace('"','').replace("'","").split('=')),
        map(lambda x: (x[0].strip().replace(' ',''),
                       x[1].strip().replace('\x92',"'").replace(',',''))),
        filter(lambda x: x[0].find('-') == -1),
        (mapcat, lambda x: map(lambda y: (y, x[1]), x[0].split(','))),
        filter(lambda x: x[0].isnumeric()),
        map(lambda x: (int(x[0]), x[1])),
        dict
    )
    return d


def parse_variable_levels(levels_f, remote_url = True):
    fh = levels_f
    if remote_url:
        r = requests.get(levels_f)
        fh = r.text
    levels = thread_last(
        fh.split(';'),
        map(lambda x: re.split('\/?\*', x)[0].strip()),
        filter(lambda x: x.lower().startswith('value')),
        map(lambda x: x.split('\n')),
        map(lambda x: (x[0].split()[1].lower(), block2dict(x[1:]))),
        dict
    )
    return levels

#@dask.delayed
def load_brfss_varlabels( row ):
    assignments = parse_format_assignments(row['formas'])
    #logger.info("%s: loading variable levels" % (row['year']))
    levels = parse_variable_levels(row['format'])
    '''
    df = pd.concat([
        (pd.DataFrame(
            list(levels[v].items()),
            columns=['code','label'])
        .assign(var=k, year=int(row['year']))
        ) for
        k, v in assignments.items() if v in levels
    ])
    df['code'] = df['code'].astype(int)
    df['var'] = df['var'].astype('str')
    df['label'] = df['label'].astype('category')
    df = df.set_index(['var','year','code'])
    '''
    #logger.info(df.shape)
    #logger.info(df.head())
    #logger.ingo(df.describe())
    return {k: levels[v] for k, v in assignments.items() if v in levels}

def get_sitecodes(src, type):
    if type == 'fips':
        src = src.apply(lambda x: us.states.lookup( '%.2d' % x ).abbr if
                        int(x) in US_STATES_FIPS_INTS else
                        MISSING_INDICATOR).astype(str)
    else:
        raise KeyError('Only fips is supported for sitecode type.')

@retry(tries=3, delay=5, backoff=2)
def load_sas_from_remote_zip(url, format):
    with zipfile.ZipFile( io.BytesIO(
        urllib.request.urlopen(url).read() )) as zipf:
        with zipf.open(zipf.namelist()[0]) as fh:
            return pd.read_sas(fh, format=format)

#@dask.delayed
def load_sas_survey_df( row ):
    logger.info("%s: loading SAS annotation files" % (row['year']))
    lbls = load_brfss_varlabels( row )
    logger.info(len(lbls.keys()))
    logger.info("%s: loading SAS export file" % (row['year']))
    df = load_sas_from_remote_zip(row['xpt'], 'xport')
    df.rename(columns={x: x.lower() for x in df.columns}, inplace=True)
    logger.info("%s: loaded SAS export file with %d rows, %d cols" %
                (row['year'], df.shape[0], df.shape[1]))
    df = (df.head(1000).select_dtypes(include=[int,float])
            .apply(lambda x: pd.to_numeric(x.fillna(-1), errors='coerce', downcast='integer'))
            .assign(year = int(row['year']),
                    sitecode = df[row['sitecode_col']].astype(int),
                    weight = df[row['weight_col']].astype(float),
                    strata = df[row['strata_col']].astype(int),
                    psu = df[row['psu_col']].astype(int)))
    logger.info('replacing levels')
    lbls = {k:v for k,v in lbls.items() if k in df.columns}
    logger.info(df.dtypes.value_counts())
    df.replace(to_replace=lbls, inplace=True)
    logger.info(df.dtypes.value_counts())
    str_cols = df.columns[df.apply(lambda x: x.apply(type) == str).all()]
    logger.info(df.dtypes.value_counts())
    logger.info('done')
    return df


def process_sas_survey_dataset(files):
    flist = pd.read_csv(files, comment='#')
    dfs = [load_brfss_df(r) for idx, r in list(flist.iterrows())[:2]]
    logger.info('merging dataframes')
    dfs = pd.concat(dfs, ignore_index=True).fillna(-1)
    cat_cols = dfs.select_dtypes(['object','category']).columns
    dfs = (dfs[cat_cols].astype({k:'category' for k in cat_cols}, errors='raise')
           .assign(year=pd.to_numeric(dfs['year'], errors='coerce',
                                      downcast='integer'),
                   sitecode=dfs['sitecode'].astype('category'),
                   weight=pd.to_numeric(dfs['weight'], errors='coerce',
                                        downcast='float'),
                   strata=pd.to_numeric(dfs['strata'],errors='coerce',
                                        downcast='integer'),
                   psu=pd.to_numeric(dfs['psu'], errors='coerce',
                                     downcast='integer')))
    logger.info(dfs.shape)
    feather.write_dataframe(dfs, 'brfss_test.feather')

if __name__ == '__main__':
    from dask.distributed import Executor, Client
    f = '~/dev/semanticbits/survey_stats/data/brfss/annual_survey_files.csv'
    #client = Client('127.0.0.1:8786')
    process_brfss_dataset(f)



