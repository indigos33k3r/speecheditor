try:
    import simplejson as json
except:
    import json
import urllib
import subprocess
import os

import sys
sys.path.append("/home/ubuntu/speecheditor")
sys.path.append("/var/www/html/srubin/speecheditor")

import numpy as N

import reauthor_speech
import duplicate_lines
from music_remix.music_remix import MusicGraph
from cubic_spline import MonotonicCubicSpline

from radiotool.composer import\
    Track, Song, Speech, Composition, Segment, RawVolume


try:
    from app_path import APP_PATH
except:
    APP_PATH = ''

from flask import Flask, request, make_response, jsonify, Response
from werkzeug import secure_filename
app = Flask(__name__)

@app.route('/reauthor', methods=['POST'])
def reauthor():
    if request.method == 'POST':
        post_data = urllib.unquote(request.data)
        dat = json.loads(post_data)
        
        tracks = dat["exportedTimeline"]
        result = {}
        
        c = Composition(channels=1)
        
        for t in tracks:
            if t["waveformClass"] == "textAlignedWaveform":
                score_start = t["scoreStart"]
                with open(APP_PATH + 'static/' + dat["speechText"], 'r') as f:
                    af = json.loads(f.read())["words"]
                ef = dat["speechReauthor"]["words"]

                crossfades = True
                if "crossfades" in dat:
                    crossfades = dat["crossfades"]

                result = reauthor_speech.rebuild_audio(
                    APP_PATH + 'static/' + dat["speechAudio"],
                    af, ef,
                    cut_to_zc=True,
                    tracks_and_segments=True,
                    samplerate=dat["speechSampleRate"],
                    score_start=score_start,
                    crossfades=crossfades
                )
                
                c.add_tracks(result["tracks"])
                c.add_score_segments(result["segments"])
            
            elif t["waveformClass"] == "musicWaveform":
                # handle music authored per beat
                starts = t["extra"]["starts"]
                durs = t["extra"]["durations"]
                dists = t["extra"]["distances"]
                vx = N.array(t["extra"]["volume"]["x"])
                vy = N.array(t["extra"]["volume"]["y"])
                    
                score_start = t["scoreStart"]
                filename = APP_PATH + "static/" + t["filename"]
                
                if filename.lower().endswith('.mp3'):
                    wav_fn = ".".join(filename.split('.')[:-1]) + ".wav"
                    if not os.path.isfile(wav_fn):
                        subprocess.call('lame --decode "%s"'
                            % filename, shell=True)
                
                track = Track(wav_fn, t["name"])
                c.add_track(track)
                current_loc = float(score_start)
                
                # create the spline interpolator
                vx = vx / 1000.0 * track.sr()
                # cdf = MonotonicCubicSpline(vx, vy)
                
                
                segments = []
                cf_durations = []
                seg_start = starts[0]
                seg_start_loc = current_loc
                
                for i, start in enumerate(starts):
                    if i == 0 or dists[i - 1] == 0:
                        dur = durs[i]
                        current_loc += dur
                    else:
                        seg = Segment(track, seg_start_loc, seg_start,
                            current_loc - seg_start_loc)
                        
                        c.add_score_segment(seg)
                        segments.append(seg)
                        
                        track = Track(wav_fn, t["name"])
                        c.add_track(track)
                        dur = durs[i]
                        cf_durations.append(dur)
                        
                        seg_start_loc = current_loc
                        seg_start = start
                        
                        current_loc += dur
                        
                last_seg = Segment(track, seg_start_loc, seg_start,
                    current_loc - seg_start_loc)
                c.add_score_segment(last_seg)
                segments.append(last_seg)
                
                all_segs = []
                
                # no repeated values
                if vx[0] == vx[1]:
                    vx = vx[1:]
                    vy = vy[1:]
                
                for i, seg in enumerate(segments[:-1]):
                    rawseg = c.cross_fade(seg, segments[i + 1], cf_durations[i])
                    
                    all_segs.extend([seg, rawseg])
                all_segs.append(segments[-1])

                first_loc = all_segs[0].score_location
                
                vx[-1] = all_segs[-1].score_location -\
                         first_loc + all_segs[-1].duration
                
                cdf = MonotonicCubicSpline(vx, vy)
                
                for seg in all_segs:
                    vol_frames = N.empty(seg.duration)
                    
                    samplex = N.arange(seg.score_location - first_loc,
                        seg.score_location - first_loc + seg.duration,
                        10000)
                        
                    sampley = N.array([cdf.interpolate(x) for x in samplex])
                    
                    for i, sy in enumerate(sampley):
                        print i, sy
                        if i != len(samplex) - 1:
                            vol_frames[i * 10000:(i + 1) * 10000] =\
                                 N.linspace(sy, sampley[i + 1], num=10000)
                        else:
                            vol_frames[i * 10000:] =\
                                N.linspace(sy, vy[-1],
                                           num=seg.duration - i * 10000)
                    
                    vol = RawVolume(seg, vol_frames)
                    c.add_dynamic(vol)

            
            elif t["waveformClass"] == "waveform":
                score_start = t["scoreStart"]
                track_start = t["wfStart"]
                duration = t["duration"]
                filename = APP_PATH + "static/" + t["filename"]
                
                wav_fn = filename
                
                if filename.lower().endswith('.mp3'):
                    wav_fn = ".".join(filename.split('.')[:-1]) + ".wav"
                    if not os.path.isfile(wav_fn):
                        subprocess.call('lame --decode "%s"'
                            % filename, shell=True)

                    
                track = Track(wav_fn, t["name"])
                segment = Segment(track, score_start, track_start, duration)
                
                c.add_track(track)
                c.add_score_segment(segment)
        
        c.output_score(
            adjust_dynamics=False,
            filename=APP_PATH + "static/tmp/" + dat["outfile"],
            channels=1,
            filetype='wav',
            samplerate=result["samplerate"],
            separate_tracks=False)
        
        subprocess.call('lame -f -b 128 ' + APP_PATH + 'static/tmp/'
            + dat["outfile"] + '.wav', shell=True)
        
        # get the new wav2json data, maybe

        subprocess.call('rm ' + APP_PATH + 'static/tmp/' +
            dat["outfile"] + '.wav', shell=True)
        return jsonify(url='tmp/' + dat["outfile"] + '.mp3',
                       timing=result["timing"])


@app.route('/ping')
def ping():
    return str(sys.path)


@app.route('/download/<name>')
def download(name):
    # should investigate flask's send_file
    
    resp = make_response(
        open(APP_PATH + 'static/tmp/' + name + '.mp3', 'r').read())
    resp.headers['Content-Type'] = 'audio/mpeg'
    resp.headers['Pragma'] = 'public'
    resp.headers['Expires'] = '0'
    resp.headers['Cache-Control'] = 'must-revalidat, post-check=0, pre-check=0'
    resp.headers['Cache-Control'] = 'public'
    resp.headers['Content-Description'] = 'File Transfer'
    resp.headers['Content-Disposition'] =\
        'attachment; filename=' + name + '.mp3'
    resp.headers['Content-Transfer-Encoding'] = 'binary'
    resp.headers['Content-Length'] =\
        os.stat(APP_PATH + 'static/tmp/' + name + '.mp3').st_size
    return resp


@app.route('/dupes', methods=['POST'])
def dupes():
    if request.method == 'POST':
        post_data = urllib.unquote(request.data)
        dat = json.loads(post_data)
        with open(APP_PATH + 'static/' + dat["speechText"], 'r') as f:
            af = json.load(f)["words"]

        return Response(json.dumps(duplicate_lines.get_dupes(af)),
            mimetype="application/json")
    return


@app.route('/uploadSong', methods=['POST'])
def upload_song():
    if request.method == 'POST':
        upload_path = APP_PATH + 'static/uploads/'
        f = request.files['song']
        file_path = f.filename.replace('\\', '/')
        filename = secure_filename(f.filename)
        full_name = upload_path + filename
        f.save(full_name)
        
        # convert to wav
        subprocess.call(
            'lame --decode "%s"' % full_name, shell=True)
                
        wav_name = ".".join(full_name.split('.')[:-1]) + '.wav'
        
        # wav2json
        subprocess.call(
            'wav2json -p 2 -s 10000 --channels mid -n -o "%s" "%s"' %
            (upload_path + 'wfData/' + filename + '.json', wav_name),
            shell=True)

        out = {
            "path": "uploads/" + filename,
            "name": file_path.split('.')[0]
        }

        # get length of song upload
        track = Track(wav_name, "track")
        out["dur"] = track.total_frames() / float(track.sr()) * 1000.0

        # get song graph
        mg = MusicGraph(full_name, cache_path=upload_path, verbose=True)        
        out["graph"] = mg.json_graph()
        
        print "Got music graph"

        # delete wav
        #
        # actually, don't delete the wav for now.
        # subprocess.call('rm "%s"' % wav_name, shell=True)

        # read waveform data json
        with open(upload_path + 'wfData/' + filename + '.json', 'r') as wf:
            out["wfData"] = json.load(wf)["mid"]

        return jsonify(**out)
    return


@app.route('/alignment/<name>')
def alignment(name):
    try:
        out = json.load(
            open("%sstatic/%s-breaths.json" % (APP_PATH, name), 'r'))
    except Exception, e:
        print e
        algn = json.load(
            open("%sstatic/%s.json" % (APP_PATH, name), 'r'))["words"]
        new_alignment = reauthor_speech.render_pauses(
            "%sstatic/%s44.wav" % (APP_PATH, name), algn)
        out = {"words": new_alignment}
        json.dump(out,
            open('%sstatic/%s-breaths.json' % (APP_PATH, name), 'w'))

    out["speechText"] = '%s-breaths.json' % name
    return jsonify(**out)


if __name__ == '__main__':
    app.run(debug=True)
