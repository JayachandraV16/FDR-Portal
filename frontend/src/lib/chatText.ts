import type { ChatLanguage } from "./api";

/**
 * The words of the Copilot panel itself, in the three supported languages. The answers come from the server in
 * the chosen language; these are only the buttons and labels around them. Names of pages and buttons elsewhere
 * in the (English) portal are kept in English inside the sentences so users can find them.
 * Hindi and Marathi wording needs review by native speakers before an official demonstration.
 */
export interface ChatText {
  launcher: string;
  title: string;
  tagline: string;
  clear: string;
  close: string;
  language: string;
  question: string;
  placeholder: string;
  send: string;
  sending: string;
  thinking: string;
  intro: string;
  explain: string;
  explainSent: string;
  suggested: string;
  conversation: string;
  facts: string;
  tryAgain: string;
  builtIn: string;
  aiGuidance: (provider: string) => string;
  aiWording: (provider: string) => string;
  keys: string;
  offline: string;
  networkError: string;
  serverError: (status: number) => string;
  sessionError: string;
  generic: string;
  tooLong: string;
  // answer feedback
  helpfulQuestion: string;
  helpful: string;
  notHelpful: string;
  reasonLabel: string;
  reasons: { incorrect: string; unclear: string; incomplete: string; not_relevant: string; other: string };
  submitFeedback: string;
  thanks: string;
  feedbackError: string;
  feedbackUnavailable: string;
}

const en: ChatText = {
  launcher: "Open FDR Copilot",
  title: "FDR Copilot",
  tagline: "A read-only assistant. It explains the portal and your own records; it cannot approve, release or change anything.",
  clear: "Clear conversation",
  close: "Close",
  language: "Language",
  question: "Your question",
  placeholder: "Ask a question…",
  send: "Send",
  sending: "Sending…",
  thinking: "FDR Copilot is thinking…",
  intro: "Hello, I'm FDR Copilot. Ask me how something works, or about the FDRs you can see. Answers about records come from the portal's data, and nothing here is bank-verified.",
  explain: "Explain this page",
  explainSent: "Explain this page",
  suggested: "Suggested questions",
  conversation: "Conversation",
  facts: "Facts from your records",
  tryAgain: "Try again",
  builtIn: "Built-in answer",
  aiGuidance: (p) => `AI-generated general guidance${p === "gemini" ? " (Gemini)" : ""}. It is not verified record data or a compliance decision.`,
  aiWording: (p) => `AI-assisted wording${p === "gemini" ? " (Gemini)" : ""}; facts come from the portal's records.`,
  keys: "Enter to send, Shift+Enter for a new line",
  offline: "You are offline. Messages cannot be sent until the connection returns.",
  networkError: "Cannot reach the server. Check your connection and try again.",
  serverError: (status) => `The assistant is unavailable right now (HTTP ${status}). The rest of the portal is not affected.`,
  sessionError: "The assistant could not be used: your session may need attention.",
  generic: "Something went wrong. Please try again.",
  tooLong: "That message could not be sent. Please shorten or rephrase it.",
  helpfulQuestion: "Was this answer helpful?",
  helpful: "Helpful",
  notHelpful: "Not helpful",
  reasonLabel: "What went wrong? (optional)",
  reasons: { incorrect: "Incorrect", unclear: "Unclear", incomplete: "Incomplete", not_relevant: "Not relevant", other: "Other" },
  submitFeedback: "Send feedback",
  thanks: "Thank you for your feedback.",
  feedbackError: "Feedback could not be sent. Please try again.",
  feedbackUnavailable: "Feedback is not available on this server yet.",
};

const hi: ChatText = {
  launcher: "FDR Copilot खोलें",
  title: "FDR Copilot",
  tagline: "केवल पढ़ने वाला सहायक। यह पोर्टल और आपके अपने रिकॉर्ड समझाता है; यह स्वीकृत, रिलीज़ या कुछ भी बदल नहीं सकता।",
  clear: "बातचीत साफ़ करें",
  close: "बंद करें",
  language: "भाषा",
  question: "आपका प्रश्न",
  placeholder: "प्रश्न पूछें…",
  send: "भेजें",
  sending: "भेजा जा रहा है…",
  thinking: "FDR Copilot सोच रहा है…",
  intro: "नमस्ते, मैं FDR Copilot हूँ। पूछिए कि कोई चीज़ कैसे काम करती है, या आपके देखने योग्य FDRs के बारे में। रिकॉर्ड के उत्तर पोर्टल के डेटा से आते हैं, और यहाँ कुछ भी बैंक से सत्यापित नहीं है।",
  explain: "इस पेज को समझाएँ",
  explainSent: "इस पेज को समझाएँ",
  suggested: "सुझाए गए प्रश्न",
  conversation: "बातचीत",
  facts: "आपके रिकॉर्ड से तथ्य",
  tryAgain: "फिर प्रयास करें",
  builtIn: "अंतर्निहित उत्तर",
  aiGuidance: (p) => `AI द्वारा बनाया गया सामान्य मार्गदर्शन${p === "gemini" ? " (Gemini)" : ""}। यह सत्यापित रिकॉर्ड डेटा या अनुपालन निर्णय नहीं है।`,
  aiWording: (p) => `AI-सहायता से शब्दांकन${p === "gemini" ? " (Gemini)" : ""}; तथ्य पोर्टल के रिकॉर्ड से हैं।`,
  keys: "भेजने के लिए Enter, नई पंक्ति के लिए Shift+Enter",
  offline: "आप ऑफ़लाइन हैं। कनेक्शन लौटने तक संदेश नहीं भेजे जा सकते।",
  networkError: "सर्वर तक नहीं पहुँच सके। अपना कनेक्शन जाँचें और फिर प्रयास करें।",
  serverError: (status) => `सहायक अभी उपलब्ध नहीं है (HTTP ${status})। पोर्टल का बाकी हिस्सा प्रभावित नहीं है।`,
  sessionError: "सहायक का उपयोग नहीं हो सका: आपके सत्र पर ध्यान देना पड़ सकता है।",
  generic: "कुछ गलत हो गया। कृपया फिर प्रयास करें।",
  tooLong: "वह संदेश नहीं भेजा जा सका। कृपया उसे छोटा करें या दूसरे शब्दों में लिखें।",
  helpfulQuestion: "क्या यह उत्तर उपयोगी था?",
  helpful: "उपयोगी",
  notHelpful: "उपयोगी नहीं",
  reasonLabel: "क्या गलत था? (वैकल्पिक)",
  reasons: { incorrect: "गलत", unclear: "अस्पष्ट", incomplete: "अधूरा", not_relevant: "प्रासंगिक नहीं", other: "अन्य" },
  submitFeedback: "प्रतिक्रिया भेजें",
  thanks: "आपकी प्रतिक्रिया के लिए धन्यवाद।",
  feedbackError: "प्रतिक्रिया नहीं भेजी जा सकी। कृपया फिर प्रयास करें।",
  feedbackUnavailable: "इस सर्वर पर प्रतिक्रिया अभी उपलब्ध नहीं है।",
};

const mr: ChatText = {
  launcher: "FDR Copilot उघडा",
  title: "FDR Copilot",
  tagline: "फक्त वाचणारा सहाय्यक. तो पोर्टल आणि तुमच्या स्वतःच्या नोंदी समजावतो; तो स्वीकारू, रिलीज करू किंवा काहीही बदलू शकत नाही.",
  clear: "संभाषण पुसा",
  close: "बंद करा",
  language: "भाषा",
  question: "तुमचा प्रश्न",
  placeholder: "प्रश्न विचारा…",
  send: "पाठवा",
  sending: "पाठवत आहे…",
  thinking: "FDR Copilot विचार करत आहे…",
  intro: "नमस्कार, मी FDR Copilot आहे. एखादी गोष्ट कशी काम करते ते विचारा, किंवा तुम्हाला दिसणाऱ्या FDRs बद्दल. नोंदींबद्दलची उत्तरे पोर्टलच्या डेटावरून येतात, आणि येथे काहीही बँकेकडून सत्यापित केलेले नाही.",
  explain: "हे पान समजावा",
  explainSent: "हे पान समजावा",
  suggested: "सुचवलेले प्रश्न",
  conversation: "संभाषण",
  facts: "तुमच्या नोंदींमधील तथ्ये",
  tryAgain: "पुन्हा प्रयत्न करा",
  builtIn: "अंगभूत उत्तर",
  aiGuidance: (p) => `AI ने तयार केलेले सर्वसाधारण मार्गदर्शन${p === "gemini" ? " (Gemini)" : ""}. हा सत्यापित नोंद डेटा किंवा अनुपालन निर्णय नाही.`,
  aiWording: (p) => `AI-सहाय्याने शब्दरचना${p === "gemini" ? " (Gemini)" : ""}; तथ्ये पोर्टलच्या नोंदींमधून आहेत.`,
  keys: "पाठवण्यासाठी Enter, नवीन ओळीसाठी Shift+Enter",
  offline: "तुम्ही ऑफलाइन आहात. कनेक्शन परत येईपर्यंत संदेश पाठवता येत नाहीत.",
  networkError: "सर्व्हरपर्यंत पोहोचता आले नाही. तुमचे कनेक्शन तपासा आणि पुन्हा प्रयत्न करा.",
  serverError: (status) => `सहाय्यक सध्या उपलब्ध नाही (HTTP ${status}). पोर्टलच्या उर्वरित भागावर परिणाम नाही.`,
  sessionError: "सहाय्यक वापरता आला नाही: तुमच्या सत्राकडे लक्ष द्यावे लागू शकते.",
  generic: "काहीतरी चुकले. कृपया पुन्हा प्रयत्न करा.",
  tooLong: "तो संदेश पाठवता आला नाही. कृपया तो लहान करा किंवा दुसऱ्या शब्दांत लिहा.",
  helpfulQuestion: "हे उत्तर उपयुक्त होते का?",
  helpful: "उपयुक्त",
  notHelpful: "उपयुक्त नाही",
  reasonLabel: "काय चुकले? (ऐच्छिक)",
  reasons: { incorrect: "चुकीचे", unclear: "अस्पष्ट", incomplete: "अपूर्ण", not_relevant: "संबंधित नाही", other: "इतर" },
  submitFeedback: "अभिप्राय पाठवा",
  thanks: "तुमच्या अभिप्रायाबद्दल धन्यवाद.",
  feedbackError: "अभिप्राय पाठवता आला नाही. कृपया पुन्हा प्रयत्न करा.",
  feedbackUnavailable: "या सर्व्हरवर अभिप्राय अद्याप उपलब्ध नाही.",
};

export const CHAT_TEXT: Record<ChatLanguage, ChatText> = { en, hi, mr };

/** Names of the languages, each written in its own language and script. */
export const LANGUAGE_NAMES: Record<ChatLanguage, string> = { en: "English", hi: "हिन्दी", mr: "मराठी" };

const STORAGE_KEY = "fdr-copilot-language";

/** The remembered language. Only the keyword is stored (never anything from a conversation). */
export function loadLanguage(): ChatLanguage {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "hi" || value === "mr" || value === "en" ? value : "en";
  } catch {
    return "en";
  }
}

export function saveLanguage(language: ChatLanguage): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // the choice still applies for this visit
  }
}

export const LANGUAGE_STORAGE_KEY = STORAGE_KEY;
