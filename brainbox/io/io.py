import time
import numpy as np
# (Previously required `os.path` to get file info before memmapping)
# import os.path as op
from ibllib.io import spikeglx
from ibllib.io.extractors.training_wheel import extract_wheel_moves
from ibllib.io.extractors.training_trials import FirstMovementTimes
from oneibl.one import ONE


def extract_waveforms(ephys_file, ts, ch, t=2.0, sr=30000, n_ch_probe=385, dtype='int16',
                      offset=0, car=True):
    '''
    Extracts spike waveforms from binary ephys data file, after (optionally)
    common-average-referencing (CAR) spatial noise.

    Parameters
    ----------
    ephys_file : string
        The file path to the binary ephys data.
    ts : ndarray_like
        The timestamps (in s) of the spikes.
    ch : ndarray_like
        The channels on which to extract the waveforms.
    t : numeric (optional)
        The time (in ms) of each returned waveform.
    sr : int (optional)
        The sampling rate (in hz) that the ephys data was acquired at.
    n_ch_probe : int (optional)
        The number of channels of the recording.
    dtype: str (optional)
        The datatype represented by the bytes in `ephys_file`.
    offset: int (optional)
        The offset (in bytes) from the start of `ephys_file`.
    car: bool (optional)
        A flag to perform CAR before extracting waveforms.

    Returns
    -------
    waveforms : ndarray
        An array of shape (#spikes, #samples, #channels) containing the waveforms.

    Examples
    --------
    1) Extract all the waveforms for unit1 with and without CAR.
        >>> import numpy as np
        >>> import brainbox as bb
        >>> import alf.io as aio
        >>> import ibllib.ephys.spikes as e_spks
        (*Note, if there is no 'alf' directory, make 'alf' directory from 'ks2' output directory):
        >>> e_spks.ks2_to_alf(path_to_ks_out, path_to_alf_out)
        # Get a clusters bunch and a units bunch from a spikes bunch from an alf directory.
        >>> clstrs_b = aio.load_object(path_to_alf_out, 'clusters')
        >>> spks_b = aio.load_object(path_to_alf_out, 'spikes')
        >>> units_b = bb.processing.get_units_bunch(spks, ['times'])
        # Get the timestamps and 20 channels around the max amp channel for unit1, and extract the
        # two sets of waveforms.
        >>> ts = units_b['times']['1']
        >>> max_ch = max_ch = clstrs_b['channels'][1]
        >>> if max_ch < 10:  # take only channels greater than `max_ch`.
        >>>     ch = np.arange(max_ch, max_ch + 20)
        >>> elif (max_ch + 10) > 385:  # take only channels less than `max_ch`.
        >>>     ch = np.arange(max_ch - 20, max_ch)
        >>> else:  # take `n_c_ch` around `max_ch`.
        >>>     ch = np.arange(max_ch - 10, max_ch + 10)
        >>> wf = bb.io.extract_waveforms(path_to_ephys_file, ts, ch, car=False)
        >>> wf_car = bb.io.extract_waveforms(path_to_ephys_file, ts, ch, car=True)
    '''

    # (Previously memmaped the file manually, but now use `spikeglx.Reader`)
    # item_bytes = np.dtype(dtype).itemsize
    # n_samples = (op.getsize(ephys_file) - offset) // (item_bytes * n_ch_probe)
    # file_m = np.memmap(ephys_file, shape=(n_samples, n_ch_probe), dtype=dtype, mode='r')

    # Get memmapped array of `ephys_file`
    s_reader = spikeglx.Reader(ephys_file)
    file_m = s_reader.data  # the memmapped array
    n_wf_samples = np.int(sr / 1000 * (t / 2))  # number of samples to return on each side of a ts
    ts_samples = np.array(ts * sr).astype(int)  # the samples corresponding to `ts`
    t_sample_first = ts_samples[0] - n_wf_samples
    t_sample_last = ts_samples[-1] + n_wf_samples

    # Exception handling for impossible channels
    ch = np.asarray(ch)
    ch = ch.reshape((ch.size, 1)) if ch.size == 1 else ch
    if np.any(ch < 0) or np.any(ch > n_ch_probe):
        raise Exception('At least one specified channel number is impossible. The minimum channel'
                        ' number was {}, and the maximum channel number was {}. Check specified'
                        ' channel numbers and try again.'.format(np.min(ch), np.max(ch)))

    # TODO car should be a separate function
    if car:  # compute spatial noise in chunks
        # (previously computed temporal noise also, but was too costly)
        # Get number of chunks.
        n_chunk_samples = 5e6  # number of samples per chunk
        n_chunks = np.ceil((t_sample_last - t_sample_first) / n_chunk_samples).astype('int')
        # Get samples that make up each chunk. e.g. `chunk_sample[1] - chunk_sample[0]` are the
        # samples that make up the first chunk.
        chunk_sample = np.arange(t_sample_first, t_sample_last, n_chunk_samples, dtype=int)
        chunk_sample = np.append(chunk_sample, t_sample_last)
        noise_s_chunks = np.zeros((n_chunks, ch.size), dtype=np.int16)  # spatial noise array
        # Give time estimate for computing `noise_s_chunks`.
        t0 = time.perf_counter()
        np.median(file_m[chunk_sample[0]:chunk_sample[1], ch], axis=0)
        dt = time.perf_counter() - t0
        print('Performing spatial CAR before waveform extraction. Estimated time is {:.2f} mins.'
              ' ({})'.format(dt * n_chunks / 60, time.ctime()))
        # Compute noise for each chunk, then take the median noise of all chunks.
        for chunk in range(n_chunks):
            noise_s_chunks[chunk, :] = np.median(
                file_m[chunk_sample[chunk]:chunk_sample[chunk + 1], ch], axis=0)
        noise_s = np.median(noise_s_chunks, axis=0)
        print('Done. ({})'.format(time.ctime()))

    # Initialize `waveforms`, extract waveforms from `file_m`, and CAR.
    waveforms = np.zeros((len(ts), 2 * n_wf_samples, ch.size))
    # Give time estimate for extracting waveforms.
    t0 = time.perf_counter()
    for i in range(5):
        waveforms[i, :, :] = \
            file_m[i * n_wf_samples * 2 + t_sample_first:
                   i * n_wf_samples * 2 + t_sample_first + n_wf_samples * 2, ch].reshape(
                       (n_wf_samples * 2, ch.size))
    dt = time.perf_counter() - t0
    print('Performing waveform extraction. Estimated time is {:.2f} mins. ({})'
          .format(dt * len(ts) / 60 / 5, time.ctime()))
    for spk, _ in enumerate(ts):  # extract waveforms
        spk_ts_sample = ts_samples[spk]
        spk_samples = np.arange(spk_ts_sample - n_wf_samples, spk_ts_sample + n_wf_samples)
        # have to reshape to add an axis to broadcast `file_m` into `waveforms`
        waveforms[spk, :, :] = \
            file_m[spk_samples[0]:spk_samples[-1] + 1, ch].reshape((spk_samples.size, ch.size))
    print('Done. ({})'.format(time.ctime()))
    if car:  # perform CAR (subtract spatial noise)
        waveforms -= noise_s[None, None, :]

    return waveforms


def load_wheel_reaction_times(eid, one=None):
    """
    Return the calculated reaction times for session.  Reaction times are defined as the time
    between the go cue (onset tone) and the onset of the first substantial wheel movement.   A
    movement is considered sufficiently large if its peak amplitude is at least 1/3rd of the
    distance to threshold (~0.1 radians).

    Negative times mean the onset of the movement occurred before the go cue.  Nans may occur if
    there was no detected movement withing the period, or when the goCue_times or feedback_times
    are nan.

    Parameters
    ----------
    eid : str
        Session UUID
    one : oneibl.ONE
        An instance of ONE for loading data.  If None a new one is instantiated using the defaults.

    Returns
    ----------
    array-like
        reaction times
    """
    if one is None:
        one = ONE()

    trials = one.load_object(eid, 'trials')
    # If already extracted, load and return
    if trials and 'firstMovement_times' in trials:
        return trials['firstMovement_times'] - trials['goCue_times']
    # Otherwise load wheelMoves object and calculate
    moves = one.load_object(eid, 'wheelMoves')
    # Re-extract wheel moves if necessary
    if not moves or 'peakAmplitude' not in moves:
        wheel = one.load_object(eid, 'wheel')
        moves = extract_wheel_moves(wheel['timestamps'], wheel['position'])
    assert trials and moves, 'unable to load trials and wheelMoves data'
    firstMove_times, is_final_movement, ids = \
        FirstMovementTimes.extract_first_movement_times(moves, trials)
    return firstMove_times - trials['goCue_times']
