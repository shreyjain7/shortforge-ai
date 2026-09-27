"""Lexical cues used by the heuristic clip scorer (English-first; multilingual text still scores
through the language-independent audio/visual/structure signals and the LLM pass)."""

from __future__ import annotations

STOPWORDS = frozenset(["a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves", "just", "really", "like", "okay", "ok", "yeah", "yes", "um", "uh", "gonna", "wanna", "gotta", "kind", "sort", "thing", "things", "stuff", "also", "even", "still", "actually", "basically", "literally", "going", "get", "got", "go", "know", "think", "mean", "right", "well", "oh"])

FILLERS = frozenset({"um", "uh", "erm", "hmm", "mm", "like", "basically", "literally", "actually", "anyway"})
FILLER_PHRASES = ("you know", "i mean", "sort of", "kind of", "or something", "and stuff")

WEAK_OPENERS = frozenset({"so", "and", "but", "or", "um", "uh", "yeah", "okay", "ok", "also", "because",
                          "anyway", "well", "right", "then", "which", "like"})
DANGLING_START = frozenset({"it", "that", "this", "they", "them", "he", "she", "those", "these", "there", "its",
                            "his", "her", "their"})
CONTEXT_REFS = ("as i said", "like i said", "as i mentioned", "like i mentioned", "going back to",
                "earlier", "in the last video", "previous video", "as we discussed", "mentioned before",
                "we talked about", "i just said", "like we said")

HOOK_PHRASES = (
    "the truth is", "the problem is", "here's the thing", "here's why", "here is why", "this is why",
    "the reason", "the secret", "nobody tells you", "no one tells you", "nobody talks about", "most people",
    "everyone thinks", "you won't believe", "i was wrong", "the biggest", "the best", "the worst",
    "the craziest", "the most", "what if", "imagine", "turns out", "it turns out", "did you know",
    "have you ever", "the moment", "i never", "never again", "stop", "don't", "you need to", "you have to",
    "the only", "biggest mistake", "mistake", "wrong", "changed everything", "changes everything",
    "game changer", "the first time", "let me tell you", "here's what", "what happened", "true story",
    "i can't believe", "hot take", "unpopular opinion", "controversial", "the real", "honestly",
)
QUESTION_OPENERS = frozenset({"what", "why", "how", "who", "when", "where", "which", "is", "are", "do", "does",
                              "did", "can", "could", "would", "should", "have", "has", "will"})
CURIOSITY_WORDS = frozenset({"why", "how", "secret", "hidden", "mystery", "surprising", "surprise", "weird",
                             "strange", "unexpected", "actually", "really", "truth", "reason", "problem",
                             "question", "discover", "discovered", "figure", "revealed", "reveal", "never",
                             "nobody", "impossible", "crazy", "insane"})
EMOTION_WORDS = frozenset({
    "love", "hate", "amazing", "incredible", "insane", "crazy", "wild", "terrible", "horrible", "awful",
    "beautiful", "shocking", "shocked", "scared", "afraid", "terrified", "angry", "furious", "sad", "cried",
    "crying", "laugh", "laughed", "hilarious", "funny", "died", "dead", "death", "kill", "killed", "fired",
    "broke", "rich", "poor", "won", "lost", "lose", "win", "huge", "massive", "dangerous", "disaster",
    "unbelievable", "ridiculous", "absurd", "brilliant", "genius", "stupid", "insanely", "perfect",
    "destroyed", "obsessed", "panic", "excited", "exciting", "epic", "legendary", "nightmare", "wow",
    "omg", "damn", "hell", "unreal", "mindblowing", "mind-blowing", "heartbreaking", "proud", "worst",
    "best", "biggest", "impossible", "fail", "failed", "failure", "success", "million", "billion",
})
PAYOFF_PHRASES = (
    "that's why", "that is why", "which means", "the answer is", "the answer", "and that's", "turns out",
    "in the end", "at the end of the day", "the lesson", "so basically", "that's how", "that's what",
    "and it worked", "and that was", "finally", "the result", "so yes", "so no", "bottom line",
    "the point is", "that's the", "which is why", "and so", "and then", "boom", "and guess what",
    "long story short", "the takeaway",
)
LAUGHTER = ("[laughter]", "(laughs)", "(laughter)", "[laughs]", "haha", "hahaha", "lol", "[laughing]")
SPONSOR_PHRASES = (
    "sponsor", "sponsored", "brought to you by", "use code", "promo code", "discount code", "link in the description",
    "link below", "check out", "sign up", "free trial", "percent off", "% off", "first month", "today's video is",
    "thanks to", "partnered with", "affiliate", "patreon", "merch", "subscribe", "hit the bell", "like and subscribe",
)
OUTRO_PHRASES = ("thanks for watching", "thank you for watching", "see you next", "see you in the next",
                 "that's it for", "that's all for", "until next time", "peace out", "catch you")
INTRO_PHRASES = ("welcome back", "welcome to", "hey guys", "hey everyone", "what's up", "in today's video",
                 "today we're", "today i'm", "in this video")
