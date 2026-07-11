import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const [baseUrl,outputRoot,storyList]=process.argv.slice(2);
if(!baseUrl||!outputRoot||!storyList)throw new Error('usage: capture_browser.mjs BASE_URL OUTPUT_ROOT A,B,...');

const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-default-apps','--disable-features=Translate,MediaRouter','--disable-sync']});
const context=await browser.newContext({viewport:{width:1440,height:810},deviceScaleFactor:1,locale:'en-US',timezoneId:'UTC',colorScheme:'light',reducedMotion:'reduce'});
const page=await context.newPage();
const problems=[];
page.on('console',message=>{if(['error','warning'].includes(message.type()))problems.push(`console ${message.type()}: ${message.text()}`);});
page.on('pageerror',error=>problems.push(`pageerror: ${error.message}`));
page.on('requestfailed',request=>problems.push(`requestfailed: ${request.url()} ${request.failure()?.errorText}`));
await page.goto(baseUrl,{waitUntil:'networkidle'});
await page.waitForFunction(()=>window.__MAPF_SPLICE_CAPTURE__?.getItemCount()>0);
await page.evaluate(async()=>{await document.fonts.ready;window.scrollTo(0,0);document.documentElement.style.overflow='hidden';document.body.style.overflow='hidden';});

for(const storyId of storyList.split(',')){
  await page.reload({waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.__MAPF_SPLICE_CAPTURE__?.getItemCount()>0);
  await page.evaluate(async()=>{await document.fonts.ready;window.scrollTo(0,0);document.documentElement.style.overflow='hidden';document.body.style.overflow='hidden';});
  await page.evaluate(id=>window.__MAPF_SPLICE_CAPTURE__.activateStory(id),storyId);
  await page.waitForFunction(id=>window.__MAPF_SPLICE_CAPTURE__.getStoryId()===id&&window.__MAPF_SPLICE_CAPTURE__.getItemCount()>0,storyId);
  await page.evaluate(()=>{window.__MAPF_SPLICE_CAPTURE__.restart();window.__MAPF_SPLICE_CAPTURE__.setExportView(true);});
  const items=await page.evaluate(()=>window.__MAPF_SPLICE_CAPTURE__.getItems());
  const storyDir=path.join(outputRoot,`story-${storyId.toLowerCase()}`),framesDir=path.join(storyDir,'frames');
  fs.mkdirSync(framesDir,{recursive:true});
  for(let cursor=0;cursor<items.length;cursor+=1){
    await page.evaluate(value=>window.__MAPF_SPLICE_CAPTURE__.seek(value),cursor);
    await page.waitForFunction(value=>window.__MAPF_SPLICE_CAPTURE__.getCursor()===value,cursor);
    await page.evaluate(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
    await page.screenshot({path:path.join(framesDir,`${String(cursor).padStart(4,'0')}.png`),type:'png'});
  }
  const timing={story_id:storyId,items,viewport:{width:1440,height:810,device_scale_factor:1},emitted_duration_ms:items.reduce((sum,item)=>sum+item.presentation.durationMs,0)};
  fs.writeFileSync(path.join(storyDir,'timing.json'),`${JSON.stringify(timing,null,2)}\n`);
}
await browser.close();
if(problems.length)throw new Error(`browser capture problems:\n${problems.join('\n')}`);
