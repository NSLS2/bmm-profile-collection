from databroker import catalog
from databroker.queries import TimeRange

import numpy
import matplotlib.pyplot as plt
#plt.ion()
import h5py
import os

from sklearn.neighbors import KNeighborsClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from sklearn.model_selection import train_test_split
from joblib import dump, load

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext

from BMM.larch_interface import Pandrosus
#from BMM.functions import plotting_mode
from BMM.user_ns.base import WORKSPACE, bmm_catalog
from BMM.user_ns.bmm  import BMMuser

# when pickle changes version number, this error message will happen twice:
# /opt/conda_envs/collection-2021-1.2/lib/python3.7/site-packages/sklearn/base.py:315:
#   UserWarning: Trying to unpickle estimator DecisionTreeClassifier
#   from version 0.23.1 when using version 0.24.1. This might lead to
#   breaking code or invalid results. Use at your own risk.
#   UserWarning)
#
# would be nice to add a check at self.clf = load(self.model) (line 48)
# or simply rerun clf.train() at the bsui command line.

from BMM.user_ns.base import startup_dir

class BMMDataEvaluation:
    '''A very simple machine learning model for recognizing when an XAS
    scan goes horribly awry.

    '''
    def __init__(self):
        self.GRIDSIZE = 401
        self.clf      = None
        self.scaler   = StandardScaler()
        self.trainX   = None
        self.trainy   = None
        self.X        = None
        self.y        = None
        self.folder   = os.path.join(startup_dir, 'ML')
        self.model    = os.path.join(self.folder, 'data_evaluation.joblib')
        self.hdf5     = [os.path.join(self.folder, 'fluorescence_training_set.hdf5'),
                         os.path.join(self.folder, 'transmission_training_set.hdf5'),
                         os.path.join(self.folder, 'verygood_training_set.hdf5'),
                         os.path.join(self.folder, '2023-04-12_training_set.hdf5'),]
        self.matrix   = os.path.join(self.folder, 'scaler.joblib')
        self.good_emoji = ':heavy_check_mark:'
        self.bad_emoji  = ':heavy_multiplication_x:'
        self.tab = ''
        try:
            if os.path.isfile(self.model):
                self.clf = load(self.model)
            if os.path.isfile(self.matrix):
                self.scaler = load(self.matrix)
        except Exception as E:  # regenerate joblib files if python or pickle version has changed
            print(str(E))
            self.retrain()
            
    def retrain(self):
        self.tab = '\t'*4
        print(self.tab+"regenerating joblib files")
        self.import_training_set()
        self.train()
        self.clf = load(self.model)
        self.scaler = load(self.matrix)
                

    def extract_mu(self, clog=None, uid=None, mode='transmission', fig=None, ax=None, show_plot=True):
        '''Slurp a record from Databroker, contruct transmission or
        fluorescence XAS, and optionally make a plot.

        Parameters
        ----------
        clog : catalog
            catalog in which to find the record
        uid : str
            identifier string for the record in the catalog
        mode : str
            fluorescence or transmission
        fig, ax : matplotlib fig and ax
            figure and axes iof the plot
        show_plot : bool
            True to show plot
        '''
        try:
            primary = clog[uid].primary.read()
        except:
            print(f'could not read primary of {uid}')
            return None
        try:
            en = numpy.array(primary['dcm_energy'])
            if len(en) < self.GRIDSIZE/2:
                return None
            i0 = numpy.array(primary['I0'])
            if mode == 'transmission' or mode == 'verygood':
                signal = numpy.array(primary['It'])
                mu = numpy.log(abs(i0/signal))
            # elif mode == 'xs':
            #     t = user_ns['db'][-1].table()
            #     el = BMMuser.element
            #     dtc1 = numpy.array(t[el+'1'])
            #     dtc2 = numpy.array(t[el+'2'])
            #     dtc3 = numpy.array(t[el+'3'])
            #     dtc4 = numpy.array(t[el+'4'])
            #     signal = dtc1+dtc2+dtc3+dtc4
            #     mu = signal/i0
            else:
                # dtc1 = numpy.array(primary[BMMuser.dtc1])
                # dtc2 = numpy.array(primary[BMMuser.dtc2])
                # dtc3 = numpy.array(primary[BMMuser.dtc3])
                # dtc4 = numpy.array(primary[BMMuser.dtc4])
                
                if clog[uid].metadata['start']['XDI']['Element']['symbol'] == 'Ti':
                    dtc1 = numpy.array(primary['DTC2_1'])
                    dtc2 = numpy.array(primary['DTC2_2'])
                    dtc3 = numpy.array(primary['DTC2_3'])
                    dtc4 = numpy.array(primary['DTC2_4'])
                elif clog[uid].metadata['start']['XDI']['Element']['symbol'] == 'Ce':
                    dtc1 = numpy.array(primary['DTC3_1'])
                    dtc2 = numpy.array(primary['DTC3_2'])
                    dtc3 = numpy.array(primary['DTC3_3'])
                    dtc4 = numpy.array(primary['DTC3_4'])
                elif clog[uid].metadata['start']['XDI']['Element']['symbol'] == 'Fe':
                    dtc1 = numpy.array(primary['DTC1'])
                    dtc2 = numpy.array(primary['DTC2'])
                    dtc3 = numpy.array(primary['DTC3'])
                    dtc4 = numpy.array(primary['DTC4'])
                signal = dtc1+dtc2+dtc3+dtc4
                mu = signal/i0
            if show_plot:
                plt.cla()
                ax.plot(en, mu)
                fig.canvas.draw()
                fig.canvas.flush_events()
            return(en, mu)
        except Exception as exc:
            print(exc)
            return None


    def rationalize_mu(self, en, mu):
        '''Return energy and mu on a "rationalized" grid of equally spaced points.  See self.GRIDSIZE
        '''
        ee=list(numpy.arange(float(en[0]), float(en[-1]), (float(en[-1])-float(en[0]))/self.GRIDSIZE))
        mm=numpy.interp(ee, en, mu)
        return(ee, mm)


    def get_uid_list(self, mode='fluorescence'):
        '''Gather a curated list of uids to be used in the training set
        '''
        if mode == 'verygood':
            uidlist = os.path.join(self.folder, 'very_good_data')  ## the "Elements" standards set
            with open(uidlist, "r") as f:
                uidstrings = f.read()
            these = uidstrings.split('\n')
            these = catalog['bmm'].search({"uid": {"$in": these[:-1]}})
        else:
            ## xafs scans use scan_nd, linescans use rel_scan, timescans use count, areascans use grid_scan
            search_results = catalog['bmm'].search({'plan_name': 'scan_nd'})
            ## this is a weekend of measuring decent data with a long stretch of failure for the 0 part of the training set
            timequery = TimeRange(since='2020-07-09', until='2020-07-13', timezone="US/Eastern")
            these=search_results.search(timequery)
        return these

    def process_catalog(self, mode='fluorescence'):
        '''Score each entry in the training set.  This will gather a list of
        uids, plot them one-by-one, and solicit a 1/0 score for each
        one.  The properly interpolated and scored data will be stored
        in an HDF5 file for later use.

        '''
        shplot, fig, ax = False, False, False
        if mode != 'verygood':
            fig, ax = plt.subplots(1,1)
            plt.show(False)
            plt.draw()
            fig.canvas.flush_events()
            shplot = True
        these = self.get_uid_list(mode)
        #list(catalog['bmm'])
        print(f'Scoring {len(these)} records')

        h5file = os.path.join(self.folder, f'{mode}_training_set.hdf5')
        try:
            os.remove(h5file)
        except:
            pass
        f = h5py.File(h5file, 'w')

        count = 0
        for uid in list(these):
            count += 1
            ret = self.extract_mu(clog=these, uid=uid, mode=mode, fig=fig, ax=ax, show_plot=False)
            if ret is None:
                print(f'skipping {uid}, not data or too short')
            else:
                ee, mm = self.rationalize_mu(*ret)
                print(f'{count}  {len(ee)}   {uid}   {mode}')
                if len(ee) < self.GRIDSIZE:
                    continue
                elif len(ee) > self.GRIDSIZE:
                    ee = ee[:-1]
                    mm = mm[:-1]
                grp = f.create_group(uid)
                grp.create_dataset("energy", data=ee)
                grp.create_dataset("mu", data=mm)
                if mode == 'verygood':
                    grp.attrs['score'] = 1
                else:
                    action = input('\n' + bold_msg('1= good  2=bad  q=quit > '))
                    if action.lower() == 'q':
                        plt.close(fig)
                        return()
                    else:
                        grp.attrs['score'] = action
        plt.close(fig)

    def import_training_set(self):
        scores = list()
        data = list()
        for h5file in self.hdf5:
            if os.path.isfile(h5file):
                print(f'{self.tab}reading data from {h5file}')
                f = h5py.File(h5file,'r')
                for uid in f.keys():
                    try:
                        score = int(f[uid].attrs['score'])
                        if score >1:
                            score == 0
                    except:
                        continue
                    mu = list(f[uid]['mu'])
                    scores.append(score)
                    data.append(mu)

        self.trainX, self.X, self.trainy, self.y = train_test_split(data, scores, random_state=0)
        self.scaler.fit(self.trainX)
        dump(self.scaler, self.matrix)
        print(f'{self.tab}wrote scaling matrix to {self.matrix}')
        
        #self.X = self.scaler.transform(self.X)
        
    def train(self, model='rf'):
        '''Using all the hdf5 files of interpolated, scored data, create the
        evaluation model, saving it to a joblib dump file.

        '''

        print(f"{self.tab}training {model.upper()} model...")
        if model.lower() == 'knn':
            self.clf = KNeighborsClassifier(n_neighbors=1)
        elif model.lower() == 'rf':
            self.clf = RandomForestClassifier(random_state=0)
        elif model.lower() == 'mpl':
            self.clf = MLPClassifier(solver='lbfgs', hidden_layer_sizes=(10, )) #, random_state=1)
        else:
            self.clf = RandomForestClassifier(random_state=0)
        
        self.clf.fit(self.scaler.transform(self.trainX), self.trainy)
        dump(self.clf, self.model)
        print(f'{self.tab}wrote model to {self.model}')
        #self.X = X_test
        #self.y = y_test
        return()

    def score(self):
        '''Thin wrapper around the classifier object's score method.'''
        return(self.clf.score(self.scaler.transform(self.X), self.y))

    def evaluate(self, uid, mode=None):
        '''Perform an evaluation of a measurement.  The data will be
        interpolated onto the same grid used for the training set,
        then get subjected to the model.  This returns a tuple with
        the score (1 or 0) and the Slack-appropriate emoji (green
        check or red cross).

        Parameters
        ----------
        uid : str
            uid of data to be evaluated
        mode : bool
            when not None, used to specify fluorescence or transmission (for a data set that has both)

        '''
        this = user_ns['db'].v2[uid]
        if mode is None:
            mode = this.metadata['start']['XDI']['_mode'][0]
        element = this.metadata['start']['XDI']['Element']['symbol']
        t = this.primary.read()
        i0 = t['I0']
        en = t['dcm_energy']
        if mode in ('fluorescence', 'dante', 'pilatus'):
            channels = this.metadata['start']['XDI']['_dtc']
            signal = numpy.zeros(len(en))
            for ch in channels:
                signal += numpy.array(t[ch])
            mu = signal/i0
        elif mode == 'transmission':
            it = this.primary.read()['It']
            mu = numpy.log(abs(i0/it))
        elif mode == 'reference':
            it = this.primary.read()['It']
            ir = this.primary.read()['Ir']
            mu = numpy.log(abs(it/ir))
        elif mode == 'yield':
            iy = this.primary.read()['Iy']
            mu = iy/i0
        else:                   #  this is old skool analog fluorescence data.  Try to evaluate...
            if element in str(this.primary.read()['vor:vor_names_name3'][0].values):
                signal = this.primary.read()['DTC1'] + this.primary.read()['DTC2'] + this.primary.read()['DTC3'] + this.primary.read()['DTC4']
            elif element in str(this.primary.read()['vor:vor_names_name15'][0].values):
                signal = this.primary.read()['DTC2_1'] + this.primary.read()['DTC2_2'] + this.primary.read()['DTC2_3'] + this.primary.read()['DTC2_4']
            elif element in str(this.primary.read()['vor:vor_names_name19'][0].values):
                signal = this.primary.read()['DTC3_1'] + this.primary.read()['DTC3_2'] + this.primary.read()['DTC3_3'] + this.primary.read()['DTC3_4']
            else:
                print('cannot figure out fluorescence signal')
                #print(f'vor:vor_names_name3 {}')
                return()
            mu = signal/i0
        e,m = self.rationalize_mu(en, mu)
        if len(e) > self.GRIDSIZE:
            e, m = e[:-1], m[:-1]
        m = self.scaler.transform([m,])
        #if len(m) > self.GRIDSIZE:
        #    m = m[:-1]
        result = self.clf.predict(m)[0]
        if result == 1:
            return(result, self.good_emoji)
        else:
            return(result, self.bad_emoji)
    
    def test_failure(self, filename=None):
        '''Examine and process data that failed the current iteration of the data evaluator. 
        '''

        fig, ax = plt.subplots(1,1)
        plt.show()
        plt.draw()
        fig.canvas.flush_events()

        
        h5file = os.path.join(self.folder, f'{filename}_training_set.hdf5')
        try:
            os.remove(h5file)
        except:
            pass
        h5 = h5py.File(h5file, 'w')

        count = 0
        faillist = os.path.join(self.folder, filename)
        with open(faillist, 'r') as fl:
            allstr = fl.readlines()

        p = Pandrosus()
        p.folder, p.db = user_ns['BMMuser'].workspace, user_ns['db']

        for u in allstr:
            uid = u.strip()
            print(f'-->{uid}<--', end='')
            thisdata = bmm_catalog[uid]
            print('    ', thisdata)
            plan_name = thisdata.metadata['start']['plan_name']
            if 'xafs' not in plan_name:
                continue
            if 'fluorescence' in plan_name:
                mode = 'fluorescence'
            elif 'xs' in plan_name:
                mode = 'fluorescence'
            elif 'xs1' in plan_name:
                mode = 'fluorescence'
            elif 'transmission' in plan_name:
                mode = 'transmission'
            elif 'reference' in plan_name:
                mode = 'reference'
                
            #result, emoji = self.evaluate(uid, mode)
            #print(result, emoji)
            try:
                p.fetch(uid, mode=mode)
            except Exception as E:
                print(E)
                pass
            plt.cla()
            ax.plot(p.group.energy, p.group.mu)
            fig.canvas.draw()
            fig.canvas.flush_events()

            count += 1
            action = input('\n' + bold_msg('1= good  0=bad  3=skip  q=quit > '))
            if action.lower() == 'q':
                plt.close(fig)
                print()
                h5.close()
                return()
            elif action == '3':
                warning_msg(f'skipping #{count}')
                continue
            else:
                ee, mm = self.rationalize_mu(p.group.energy, p.group.mu)
                print(f'{count}  {len(ee)}   {uid}   {mode}')
                if len(ee) < self.GRIDSIZE:
                    continue
                elif len(ee) > self.GRIDSIZE:
                    ee = ee[:-1]
                    mm = mm[:-1]
                grp = h5.create_group(uid)
                grp.create_dataset("energy", data=ee)
                grp.create_dataset("mu", data=mm)
                if int(action) == 1: 
                    grp.attrs['score'] = 1
                else:
                    grp.attrs['score'] = 0
                go_msg(f'added #{count}')
            print()
        h5.close()
