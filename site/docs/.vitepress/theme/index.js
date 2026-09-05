import DefaultTheme from 'vitepress/theme'
import './custom.css'
import Home from './components/Home.vue'
import Roadmap from './components/Roadmap.vue'
import StepHeader from './components/StepHeader.vue'
import Fig from './components/Fig.vue'
import T from './components/T.vue'
import Glossary from './components/Glossary.vue'
import Checklist from './components/Checklist.vue'
import Panel from './components/Panel.vue'
import NextSteps from './components/NextSteps.vue'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('Home', Home)
    app.component('Roadmap', Roadmap)
    app.component('StepHeader', StepHeader)
    app.component('Fig', Fig)
    app.component('T', T)
    app.component('Glossary', Glossary)
    app.component('Checklist', Checklist)
    app.component('Panel', Panel)
    app.component('NextSteps', NextSteps)
  },
}
