// Turns mic audio into 16 kHz PCM16 chunks of 100 ms for the live harness.
// Runs on the audio thread. `sampleRate` is the AudioContext's rate.
class PcmChunker extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.carry = 0;
    this.out = new Int16Array(1600);
    this.filled = 0;
    this.acc = 0;
    this.count = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      // Average the input samples that fall into each output sample (simple low-pass + decimate).
      this.acc += channel[i];
      this.count += 1;
      this.carry += 1;
      if (this.carry >= this.ratio) {
        this.carry -= this.ratio;
        const value = Math.max(-1, Math.min(1, this.acc / this.count));
        this.out[this.filled++] = value < 0 ? value * 0x8000 : value * 0x7fff;
        this.acc = 0;
        this.count = 0;
        if (this.filled === this.out.length) {
          this.port.postMessage(this.out.buffer.slice(0));
          this.filled = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-chunker", PcmChunker);
