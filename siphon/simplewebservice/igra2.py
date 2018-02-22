# Copyright (c) 2013-2015 University Corporation for Atmospheric Research/Unidata.
# Distributed under the terms of the MIT License.
# SPDX-License-Identifier: MIT
"""Read upper air data from the Integrated Global Radiosonde Archive version 2."""

import datetime
import warnings
import itertools
import numpy as np
import pandas as pd
import sys

from io import BytesIO
from io import StringIO
from zipfile import ZipFile
from urllib.request import urlopen
from .._tools import get_wind_components

warnings.filterwarnings('ignore', 'Pandas doesn\'t allow columns to be created', UserWarning)


class IGRAUpperAir:
    """Download and parse data from NCEI's Integrated Radiosonde
    Archive version 2.
    """

    def __init__(self):
        """Set ftp site address and file suffix based on desired dataset"""

        self.ftpsite = 'ftp://ftp.ncdc.noaa.gov/pub/data/igra/'
        self.suffix = ''
        self.begin_date = ''
        self.end_date = ''
        self.site_id = ''

    @classmethod
    def request_data(cls, time, site_id, derived=False):
        """Retreive IGRA version 2 data for one station.

        Parameters
        --------
        site_id : str
            11-character IGRA2 station identifier

        time : datetime
           The date and time of the desired observation. If list of two times is given,
           dataframes for all dates within the two dates will be returned.

        Returns
        -------
            :class: `pandas.DataFrame` containing the data
        """

        igra2 = cls()
        
        # Set parameters for data query
        if derived:
            igra2.ftpsite = igra2.ftpsite + 'derived/derived-por/'
            igra2.suffix = igra2.suffix + '-drvd.txt'
        else:
            igra2.ftpsite = igra2.ftpsite + 'data/data-por/'
            igra2.suffix = igra2.suffix + '-data.txt'

        if type(time) == datetime.datetime:
            igra2.begin_date = time
            igra2.end_date = time
        else:
            igra2.begin_date, igra2.end_date = time

        igra2.site_id = site_id

        df, headers = igra2._get_data()

        return df, headers

    def _get_data(self):
        """Process the IGRA2 text file for observations at site_id
        matching time.

        Return:
        -------
            :class: `pandas.DataFrame` containing the body data
            :class: `pandas.DataFrame` containing the header data
        """

        # Split the list of times into begin and end dates. If only
        # one date is supplied, set both begin and end dates equal to that date.

        body, header, dates_long, dates = self._get_data_raw()

        params = self._get_fwf_params()

        df_body = pd.read_fwf(StringIO(body), **params['body'])
        df_header = pd.read_fwf(StringIO(header), **params['header'])
        df_body['date'] = dates_long

        df_body = self._clean_body_df(df_body)
        df_header = self._clean_header_df(df_header)
        df_header['date'] = dates

        return df_body, df_header

    def _get_data_raw(self):
        """Download the IGRA2 file containing site_id observations,
        and search for observations matching the time range. Returns a tuple
        with a string for the body, string for the headers, and a list of dates
        """

        with urlopen(self.ftpsite + self.site_id + self.suffix + '.zip') as url:
            f = ZipFile(BytesIO(url.read()), 'r').open(self.site_id + self.suffix)

        lines = [line.decode('utf-8') for line in f.readlines()]

        body, header, dates_long, dates = self._select_date_range(lines)

        return body, header, dates_long, dates

    def _select_date_range(self, lines):
        """Run through the data and identify lines containing
        headers within the range begin_date to end_date.

        Parameters
        -----
        lines: list
            list of lines from the IGRA2 data file
        """
        headers = []
        num_lev = []
        dates = []

        # Get indices of headers, and make a list of dates and num_lev
        for idx, line in enumerate(lines):
            if line[0] == '#':
                year, month, day, hour = map(int, line[13:26].split())

                # All soundings have YMD, most have hour
                try:
                    date = datetime.datetime(year, month, day, hour)
                except ValueError:
                    date = datetime.datetime(year, month, day)

                # Check date
                if self.begin_date <= date <= self.end_date:
                    headers.append(idx)
                    num_lev.append(int(line[32:36]))
                    dates.append(date)
                if date > self.end_date:
                    break

        if len(dates) == 0:
            # Break if no matched dates.
            # Could improve this later by showing the date range for the station.
            raise ValueError('No dates match selection.')

        # Compress body of data into a string
        begin_idx = min(headers)
        end_idx = max(headers) + num_lev[-1]

        # Make a boolean vector that selects only list indices within the time range
        selector = np.zeros(len(lines), dtype=bool)
        selector[begin_idx:end_idx+1] = True
        selector[headers] = False
        body = ''.join([line for line in itertools.compress(lines, selector)])

        selector[begin_idx:end_idx+1] = ~selector[begin_idx:end_idx+1]
        header = ''.join([line for line in itertools.compress(lines, selector)])

        # expand date vector to match length of the body dataframe.
        dates_long = np.repeat(dates, num_lev)

        return body, header, dates_long, dates

    def _get_fwf_params(self):
        """Produce a dictionary with names, colspecs, and dtype for IGRA2 data.
        Returns a dict with entries 'body' and 'header'. """

        def _cdec(power=1):
            """Make a function that takes input string in form value*10^power,
            and return as float."""
            def _cdec_power(val):
                return float(val)/10**power
            return _cdec_power

        def _cflag(val):
            """Replace alphabetic flags A and B with numeric"""
            if val == 'A':
                return 1
            elif val == 'B':
                return 2
            else:
                return 0

        def _ctime(strformat='MMMSS'):
            """Returns a function converting time string from
            MMMSS or HHMM to seconds. Returned function takes
            a string input "val"
            """

            def _ctime_strformat(val):
                time = val.strip().zfill(5)

                if int(time) < 0:
                    return np.nan
                elif int(time) == 9999:
                    return np.nan
                else:
                    if strformat == 'MMMSS':
                        minutes = int(time[0:3])
                        seconds = int(time[3:5])
                        time_seconds = minutes*60 + seconds
                    elif strformat == 'HHMM':
                        hours = int(time[0:2])
                        minutes = int(time[2:4])
                        time_seconds = hours*3600 + minutes*60
                    else:
                        sys.exit('Unrecognized time format')

                return time_seconds
            return _ctime_strformat

        def _clatlon(x):
            n = len(x)
            deg = x[0:n-4]
            dec = x[n-4:]
            return float(deg + '.' + dec)

        if self.suffix == '-drvd.txt':
            names_body = ['pressure', 'reported_height', 'calculated_height',
                          'temperature', 'temperature_gradient', 'potential_temperature',
                          'potential_temperature_gradient', 'virtual_temperature',
                          'virtual_potential_temperature', 'vapor_pressure',
                          'saturation_vapor_pressure', 'reported_relative_humidity',
                          'calculated_relative_humidity', 'u_wind', 'u_wind_gradient',
                          'v_wind', 'v_wind_gradient', 'refractive_index']

            colspecs_body = [(0, 7), (8, 15), (16, 23), (24, 31), (32, 39),
                             (40, 47), (48, 55), (56, 63), (64, 71), (72, 79),
                             (80, 87), (88, 95), (96, 103), (104, 111), (112, 119),
                             (120, 127), (128, 135), (137, 143), (144, 151)]

            conv_body = {'pressure': _cdec(power=2),
                         'reported_height': int,
                         'calculated_height': int,
                         'temperature': _cdec(),
                         'temperature_gradient': _cdec(),
                         'potential_temperature': _cdec(),
                         'potential_temperature_gradient': _cdec(),
                         'virtual_temperature': _cdec(),
                         'virtual_potential_temperature': _cdec(),
                         'vapor_pressure': _cdec(power=3),
                         'saturation_vapor_pressure': _cdec(power=3),
                         'reported_relative_humidity': int,
                         'calculated_relative_humidity': int,
                         'u_wind': _cdec(),
                         'u_wind_gradient': _cdec(),
                         'v_wind': _cdec(),
                         'v_wind_gradient': _cdec(),
                         'refractive_index': int}

            names_header = ['site_id', 'year', 'month', 'day', 'hour', 'release_time',
                            'number_levels', 'precipitable_water', 'inv_pressure',
                            'inv_height', 'inv_strength', 'mixed_layer_pressure',
                            'mixed_layer_height', 'freezing_point_pressure',
                            'freezing_point_height', 'lcl_pressure', 'lcl_height',
                            'lfc_pressure', 'lfc_height', 'lnb_pressure', 'lnb_height',
                            'lifted_index', 'showalter_index', 'k_index', 'total_totals_index',
                            'cape', 'convective_inhibition']

            colspecs_header = [(1, 12), (13, 17), (18, 20), (21, 23), (24, 26),
                               (27, 31), (31, 36), (37, 43), (43, 48), (49, 55),
                               (55, 61), (61, 67), (67, 73), (73, 79), (79, 85),
                               (85, 91), (91, 97), (97, 103), (103, 109), (109, 115),
                               (115, 121), (121, 127), (127, 133), (133, 139),
                               (139, 145), (145, 151), (151, 157)]

            conv_header = {'site_id': str,
                           'year': int,
                           'month': int,
                           'day': int,
                           'hour': int,
                           'release_time': _ctime(strformat="HHMM"),
                           'number_levels': int,
                           'precipitable_water': _cdec(power=2),
                           'inv_pressure': _cdec(power=2),
                           'inv_height': int,
                           'inv_strength': _cdec(),
                           'mixed_layer_pressure': _cdec(power=2),
                           'mixed_layer_height': int,
                           'freezing_point_pressure': _cdec(power=2),
                           'freezing_point_height': int,
                           'lcl_pressure': _cdec(power=2),
                           'lcl_height': int,
                           'lfc_pressure': _cdec(power=2),
                           'lfc_height': int,
                           'lnb_pressure': _cdec(power=2),
                           'lnb_height': int,
                           'lifted_index': int,
                           'showalter_index': int,
                           'k_index': int,
                           'total_totals_index': int,
                           'cape': int,
                           'convective_inhibition': int}

            na_vals = ['-99999']

        else:
            names_body = ['lvltyp1', 'lvltyp2', 'etime', 'pressure',
                          'pflag', 'height', 'zflag', 'temperature', 'tflag',
                          'relative_humidity', 'dewpoint_depression',
                          'direction', 'speed']

            colspecs_body = [(0, 1), (1, 2), (3, 8), (9, 15), (15, 16),
                             (16, 21), (21, 22), (22, 27), (27, 28),
                             (28, 33), (34, 39), (40, 45), (46, 51)]

            conv_body = {'lvltyp1': int,
                         'lvltyp2': int,
                         'etime': _ctime(strformat="MMMSS"),
                         'pressure': _cdec(power=2),
                         'pflag': _cflag,
                         'height': int,
                         'zflag': _cflag,
                         'temperature': _cdec(),
                         'tflag': _cflag,
                         'relative_humidity': _cdec(),
                         'dewpoint_depression': _cdec(),
                         'direction': int,
                         'speed': _cdec()}

            names_header = ['site_id', 'year', 'month', 'day', 'hour', 'release_time',
                            'number_levels', 'pressure_source_code',
                            'non_pressure_source_code',
                            'latitude', 'longitude']

            colspecs_header = [(1, 12), (13, 17), (18, 20), (21, 23), (24, 26),
                               (27, 31), (32, 36), (37, 45), (46, 54), (55, 62), (63, 71)]

            na_vals = ['-8888', '-9999']

            conv_header = {'release_time': _ctime(strformat="HHMM"),
                           'number_levels': int,
                           'latitude': _clatlon,
                           'longitude': _clatlon}

        return {'body': {'names': names_body,
                         'colspecs': colspecs_body,
                         'converters': conv_body,
                         'na_values': na_vals,
                         'index_col': False},
                'header': {'names': names_header,
                           'colspecs': colspecs_header,
                           'converters': conv_header,
                           'na_values': na_vals,
                           'index_col': False}}

    def _clean_body_df(self, df):
        """Format the dataframe, remove empty rows, and add units attribute."""

        if self.suffix == '-drvd.txt':

            df.units = {'pressure': 'hPa',
                        'reported_height': 'm',
                        'calculated_height': 'm',
                        'temperature': 'K',
                        'temperature_gradient': 'K/km',
                        'potential_temperature': 'K',
                        'potential_temperature_gradient': 'K/km',
                        'virtual_temperature': 'K',
                        'virtual_potential_temperature': 'K',
                        'vapor_pressure': 'Pa',
                        'saturation_vapor_pressure': 'Pa',
                        'reported_relative_humidity': '%',
                        'calculated_relative_humidity': '%',
                        'u_wind': 'm/s',
                        'u_wind_gradient': '(m/s) / km)',
                        'v_wind': 'meter/second',
                        'v_wind_gradient': '(m/s) / km)',
                        'refractive_index': 'unitless'}

            df = df.dropna(subset=('temperature', 'reported_relative_humidity',
                                   'u_wind', 'v_wind'), how='all').reset_index(drop=True)

        else:
            df['u_wind'], df['v_wind'] = get_wind_components(df['speed'],
                                                             np.deg2rad(df['direction']))
            df['u_wind'] = np.round(df['u_wind'], 1)
            df['v_wind'] = np.round(df['v_wind'], 1)
            df['dewpoint'] = df['temperature'] - df['dewpoint_depression']

            df.drop('dewpoint_depression', axis=1, inplace=True)
            df = df.dropna(subset=('temperature', 'dewpoint', 'direction', 'speed',
                                   'u_wind', 'v_wind'), how='all').reset_index(drop=True)

            df.units = {'etime': 's',
                        'pressure': 'hPa',
                        'height': 'm',
                        'temperature': 'degC',
                        'dewpoint': 'degC',
                        'direction': 'degrees',
                        'speed': 'm/s',
                        'u_wind': 'm/s',
                        'v_wind': 'm/s'}

        return df

    def _clean_header_df(self, df):
        """Format the header dataframe and add units"""
        if self.suffix == '-drvd.txt':
            df.units = {'release_time': 's',
                        'precipitable_water': 'mm',
                        'inv_pressure': 'hPa',
                        'inv_height': 'm',
                        'inv_strength': 'K',
                        'mixed_layer_pressure': 'hPa',
                        'mixed_layer_height': 'm',
                        'freezing_point_pressure': 'hPa',
                        'freezing_point_height': 'm',
                        'lcl_pressure': 'hPa',
                        'lcl_height': 'm',
                        'lfc_pressure': 'hPa',
                        'lfc_height': 'm',
                        'lnb_pressure': 'hPa',
                        'lnb_height': 'm',
                        'lifted_index': 'degC',
                        'showalter_index': 'degC',
                        'k_index': 'degC',
                        'total_totals_index': 'degC',
                        'cape': 'J/kg',
                        'convective_inhibition': 'J/kg'}

        else:
            df.units = {'release_time': 's',
                        'latitude': 'degrees',
                        'latitude': 'degrees'}

        return df
