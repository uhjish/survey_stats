#!/bin/bash

setup_miniconda (){
    # setup miniconda3
    wget https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh \
        -O miniconda.sh
    chmod +x miniconda.sh
    ./miniconda.sh -b -p $HOME/miniconda
    # setup paths for install
    export PATH="$HOME/miniconda/bin:$PATH"
}

conda

if [ $? -eq 0 ]; then
    echo "found miniconda, skipping the install..."
else
    echo "couldn't find miniconda on the path, installing..."
    setup_miniconda
fi

CURDIR=`pwd`

echo "update conda and add intel channel"
conda update -q conda
conda info -a
conda config --add channels intel

echo "create conda env with intel python 3.6 and gnu r 3.4.1"
conda create -q -p venv -c intel/label/test python=3 r-base=3.4.1 pandas scikit-learn cython r-feather libiconv r-survival r-dbi

echo "activate the env and install package and dev requirements with pip"
source activate $CURDIR/venv
pip install -r requirements-dev.txt
pip install -e .

