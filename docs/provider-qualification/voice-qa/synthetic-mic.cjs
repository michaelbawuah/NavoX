/* Input fixtures only. Never records a user's microphone or remote provider audio. */
function installSyntheticMic() {
  const devices = navigator.mediaDevices;
  if (!devices || typeof devices.getUserMedia !== 'function') {
    throw new Error('Synthetic speech requires a secure browser media surface');
  }
  const original = devices.getUserMedia.bind(devices);
  const state = window.__qaVoice = {
    contextsCreated: 0, contextsClosed: 0, fixturePlays: 0,
    injectedSamples: 0, closed: false, current: null, peers: [],
    playbackResolved: 0, dataFrames: 0, dataBytes: 0,
    remoteAudioTracks: new Set(), attemptedAudioElements: new Set(), playingAudioElements: new Set(),
  };
  devices.getUserMedia = async function(constraints) {
    if (!constraints || !constraints.audio || constraints.video) {
      throw new Error('Synthetic fixture permits audio only');
    }
    // Chromium is explicitly launched with synthetic-silent devices. This call
    // exercises the normal permission surface; its track is never published.
    const permission = await original(constraints);
    permission.getTracks().forEach(track => track.stop());
    const Audio = window.AudioContext || window.webkitAudioContext;
    const context = new Audio({ sampleRate: 48000 });
    const destination = context.createMediaStreamDestination();
    await context.resume();
    const track = destination.stream.getAudioTracks()[0];
    const record = { context, destination, track, sources: new Set(), closed: false };
    state.contextsCreated++;
    state.current = record;
    const nativeStop = track.stop.bind(track);
    Object.defineProperty(track, 'stop', { configurable: true, value() {
      if (!record.closed) {
        record.closed = true;
        for (const source of record.sources) {
          try { source.stop(); } catch {}
          try { source.disconnect(); } catch {}
        }
        record.sources.clear();
        context.close().catch(() => {});
        state.contextsClosed++;
        if (state.current === record) state.current = null;
      }
      nativeStop();
    } });
    return destination.stream;
  };
  window.__qaSpeakFixture = async function(encodedWave) {
    const record = state.current;
    if (!record || record.closed || record.track.readyState !== 'live') {
      throw new Error('No current synthetic microphone track');
    }
    if (typeof encodedWave !== 'string' || encodedWave.length > 2800000) {
      throw new Error('Synthetic fixture exceeded its byte bound');
    }
    const raw = atob(encodedWave);
    const wave = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index++) wave[index] = raw.charCodeAt(index);
    const buffer = await record.context.decodeAudioData(wave.buffer);
    if (buffer.numberOfChannels !== 1 || !Number.isFinite(buffer.duration) || buffer.duration > 22) {
      throw new Error('Synthetic fixture exceeded its audio bound');
    }
    await record.context.resume();
    const source = record.context.createBufferSource();
    source.buffer = buffer;
    source.connect(record.destination);
    record.sources.add(source);
    state.fixturePlays++;
    state.injectedSamples += buffer.length;
    return new Promise((resolve, reject) => {
      source.onended = () => {
        record.sources.delete(source);
        source.disconnect();
        source.buffer = null;
        if (record.closed) reject(new Error('Synthetic microphone ended during input'));
        else resolve({ durationMs: Math.round(buffer.duration * 1000), samples: buffer.length });
      };
      source.start();
    });
  };
  const NativePeer = window.RTCPeerConnection;
  if (NativePeer) {
    window.RTCPeerConnection = new Proxy(NativePeer, { construct(Target, args) {
      const peer = new Target(...args);
      state.peers.push(peer);
      peer.addEventListener('track', event => {
        if (event.track.kind === 'audio') state.remoteAudioTracks.add(event.track);
      });
      const observe = channel => channel.addEventListener('message', event => {
        state.dataFrames++;
        state.dataBytes += typeof event.data === 'string' ? event.data.length : event.data?.byteLength || event.data?.size || 0;
      });
      peer.addEventListener('datachannel', event => observe(event.channel));
      const create = peer.createDataChannel.bind(peer);
      peer.createDataChannel = (...args) => { const channel = create(...args); observe(channel); return channel; };
      return peer;
    } });
  }
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function(...args) {
    if (this.tagName === 'AUDIO' && !this.muted) {
      for (const track of this.srcObject?.getAudioTracks?.() || []) {
        if (state.remoteAudioTracks.has(track) && track.readyState === 'live') state.attemptedAudioElements.add(this);
      }
    }
    const result = play.apply(this, args);
    return Promise.resolve(result).then(value => {
      if (this.tagName === 'AUDIO' && !this.muted) {
        state.playbackResolved++;
        for (const track of this.srcObject?.getAudioTracks?.() || []) {
          if (state.remoteAudioTracks.has(track) && track.readyState === 'live') state.playingAudioElements.add(this);
        }
      }
      return value;
    });
  };
}
module.exports = { installSyntheticMic };
