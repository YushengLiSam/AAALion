import AVFoundation

/// Singleton wrapper around AVSpeechSynthesizer for Chinese TTS.
/// Tap "朗读" on an assistant bubble → reads the message aloud.
final class TTSService {
    static let shared = TTSService()
    private let synth = AVSpeechSynthesizer()

    private init() {}

    func speak(_ text: String, language: String = "zh-CN") {
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: language)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.95
        utterance.pitchMultiplier = 1.0
        synth.speak(utterance)
    }

    func stop() {
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
    }
}
