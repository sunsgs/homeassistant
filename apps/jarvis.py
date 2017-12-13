import appdaemon.appapi as appapi
import sys
import subprocess
from subprocess import Popen, PIPE, STDOUT
import random
import string
import json
import boto3
from pathlib import Path
import os
import re
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
from fuzzywuzzy import fuzz
from fuzzywuzzy import process

######################
# Jarvis
######################

class jarvis(appapi.AppDaemon):

  def initialize(self):
    self.log("Jarvis is alive")
    self.aws_client = boto3.setup_default_session(profile_name='jarvis')
    self.aws_client = boto3.client('polly')
    self.mqtt_host = '192.168.1.19'
    self.mqtt_port = 1883
    self.mpc_host = '192.168.1.201'
    self.listen_event(self.jarvis_say, "JARVIS_SAY")
    self.listen_event(self.jarvis_set_timer, "JARVIS_SET_TIMER")
    self.listen_event(self.jarvis_playlist, "JARVIS_PLAYLIST")
    self.listen_event(self.jarvis_artist, "JARVIS_ARTIST")
    self.listen_event(self.jarvis_song, "JARVIS_SONG")

    client = mqtt.Client()
    client.on_connect = on_connect

    client.message_callback_add("hermes/nlu/intentNotParsed",
                                jarvis_unknown_intent)
    #client.message_callback_add("hermes/audioServer/default/playFinished",
    #                            playFinished)
    #client.message_callback_add("jarvis/timer_duration", timer_duration)

    client.connect(args.mqtt_host, args.port, 60)

  def jarvis_say(self, event_name, data, **args):
    self.log("jarvis_say: {}".format(data), "INFO")

    speech={ "text": data['text']}

    randomId = "".join(random.choices(string.ascii_uppercase + string.digits,
                        k=32))
    sessionId=data.get('sessionId', randomId)

    #Generating audio
    Path("/tmp/sounds/").mkdir(parents=True, exist_ok=True)

    tmp_file = Path("/tmp/sounds/"+data['text']+".wav")
    response = {}
    if not tmp_file.is_file():
      response=self.aws_client.synthesize_speech( OutputFormat='mp3', Text=data['text'], VoiceId='Geraint' )
      self.log("RESPONSE: ".format(response['RequestCharacters']), "INFO")
      mp3_file = open("/tmp/sounds/"+data['text']+".mp3", "wb")
      mp3_file.write(response['AudioStream'].read())
      mp3_file.close()
      subprocess.run(['/usr/bin/mpg123','-q','-w','/tmp/sounds/'+data['text']+'.wav', '/tmp/sounds/'+data['text']+'.mp3'])
    wav_file = open("/tmp/sounds/"+data['text']+".wav", "rb")
    audio_wav = wav_file.read()
    wav_file.close()

    publish.single('hermes/audioServer/default/playBytes/'+sessionId+'/',
      payload=audio_wav,
      hostname=self.mqtt_host,
      port=self.mqtt_port,
      protocol=mqtt.MQTTv311
    )
    #TODO: listen for callback

    # If we have a sessionId the request came from a snips dialogue
    # so let it know we are done talking
    if 'sessionId' in data:
      publish.single('hermes/tts/sayFinished',
            payload={
                "siteId": "'+data['siteId']+'",
                "sessionId": "'+data['sessionId']+'",
                "id": "'+data['id']+'"
            },
            hostname=args.host,
            port=args.port,
      )

  def jarvis_set_timer(self, event_name, data, kwargs):
    self.log("jarvis_set_timer: {}".format(data), "INFO")
    duration = int(data['hours'])*360
    duration += int(data['minutes'])*60
    duration += int(data['seconds'])

    #self.log("duration: {}".format(duration), "INFO")
    self.handle = self.run_in(
        self.jarvis_timer_done,
        duration,
        timer_name=data['name']
    )
    text = "OK, setting a timer of "

    if int(data['hours']) > 0:
        if int(data['hours']) > 1:
            text += data['hours']+" hours "
        else:
            text += " 1 hour "
        if int(data['minutes']) > 0:
            text += " and "
    if int(data['minutes']) > 0:
        if int(data['minutes']) > 1:
            text += data['minutes']+" minutes "
        else:
            text += " 1 minute "
        if int(data['seconds']) > 0:
            text += " and "
    if int(data['seconds']) > 0:
        if int(data['seconds']) > 1:
            text += data['seconds']+" seconds "
        else:
            text += " 1 second "
    text += 'for '
    text += data['name']
    #self.log("jarvis_set_timer: text: {}".format(text), "INFO")

    self.jarvis_say('NONE', {'text': text}, kwargs)

  def jarvis_cancel_timer(self, event_name, data, kwargs):
    self.log("jarvis_cancel_timer: {}".format(data), "INFO")
    try:
      self.cancel_timer(self.handle)
      speech={"text": "OK, canceling your timer"}
      self.jarvis_say('NONE', speech, data)
    except:
      speech={"text": "Sorry, I couldn't find that timer"}
      self.jarvis_say('NONE', speech, data)

  def jarvis_timer_done(self, kwargs):
    self.log("jarvis_timer_done: {}".format(kwargs))
    self.jarvis_say('NONE', {'text': "OK, your timer for "+
        kwargs['timer_name']+" is done"}, kwargs)
    #TODO: start a dialog and ask if reminder was heard

  def jarvis_playlist(self, event_name, data, kwargs):
    self.log("jarvis_playlist: {}".format(data), "INFO")
    self.call_service("media_player/shuffle_set",
                      entity_id = 'media_player.mopidy',
                      shuffle = 'true')
    self.call_service("media_player/repeat_set",
                      entity_id = 'media_player.mopidy',
                      repeat = 'true')
    source_list = self.get_state(
            "media_player.mopidy",
            "source_list")
    #self.log("jarvis_music: source_list {}".format(source_list), "INFO")
    if source_list is not None:
        matching = process.extractBests(data['playlist'],
            source_list,
            score_cutoff=60
        )
        playlists=[x[0] for x in matching]
        #self.log("jarvis_music matching playlists: {}".format(playlists),
        #         "INFO")
        playlist = random.choice(playlists)
        if not playlist:
            self.jarvis_say('NONE', {'text':
                "Sorry, I couldn't find playlist matching "
                    + data['playlist']})
            return

        self.log("jarvis_music using playlist: %s" % format(playlist),
                 "INFO")
        self.call_service("media_player/turn_on",
                          entity_id = 'media_player.mopidy')
        self.call_service("media_player/clear_playlist",
                          entity_id = 'media_player.mopidy')
        self.call_service("media_player/play_media",
            entity_id = "media_player.mopidy",
            media_content_type = "playlist",
            media_content_id = playlist
        )
        self.call_service("media_player/media_next_track",
            entity_id = "media_player.mopidy"
        )
        clean_playlist = re.sub(' \(by .*\)', '', playlist)
        self.jarvis_say('NONE', {'text': "OK, playing playlist "+
            clean_playlist})
    else:
        self.jarvis_say('NONE', {'text':
        "Sorry, I couldn't get any playlist matching "+ data['playlist']})

  def jarvis_artist(self, method, data, kwargs):
    self.log("jarvis_artist: {}".format(data), "INFO")

    artist_search=subprocess.check_output(["/usr/bin/mpc", "-h", self.mpc_host,
                "search", "artist", data['artist']],
                universal_newlines=True)
    #self.log("jarvis_artist: {}".format(artist_search), "INFO")
    tracks = [str(s) for s in str(artist_search).split('\n') if "track" in s]
    #for t in tracks:
    #    self.log("jarvis_artist: track: %s" % t )
    if tracks:
        self.call_service("media_player/media_pause",
                          entity_id = 'media_player.mopidy')

        self.call_service("media_player/clear_playlist",
                          entity_id = 'media_player.mopidy')

        mpc_add=Popen(["/usr/bin/mpc", "-h", self.mpc_host, "add"],
                        stdin=PIPE, encoding='utf8')
        mpc_add.communicate("\n".join(tracks))

        self.call_service("media_player/turn_on",
                          entity_id = 'media_player.mopidy')
        self.call_service("media_player/media_play",
                          entity_id = 'media_player.mopidy')
        self.jarvis_say('NONE', {'text':
        "OK, playing music by "+ data['artist']})
    else:
        self.jarvis_say('NONE', {'text':
        "Sorry, I couldn't find any music by "+ data['artist']})


  def jarvis_song(self, method, data, kwargs):
    self.log("jarvis_song: NOT_IMPLEMENTED {}".format(data), "INFO")

  def mqtt_on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("hermes/nlu/intentNotParsed")
