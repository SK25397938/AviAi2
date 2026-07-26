class RunwayRenderer{

    constructor(map){

        this.map=map;

    }

    metersToDegrees(lat, metersX, metersY){

        const dLat = metersY / 111320;

        const dLon = metersX / (111320 * Math.cos(lat * Math.PI / 180));

        return [

            dLon,

            dLat

        ];

    }

   runwayPolygon(runway){

    const coords=runway.geometry;

    const a=coords[0];

    const b=coords[coords.length-1];

    const lon1=a[0];
    const lat1=a[1];

    const lon2=b[0];
    const lat2=b[1];

    const R=6378137;

    const lat0=((lat1+lat2)/2)*Math.PI/180;

    const x1=R*lon1*Math.PI/180*Math.cos(lat0);
    const y1=R*lat1*Math.PI/180;

    const x2=R*lon2*Math.PI/180*Math.cos(lat0);
    const y2=R*lat2*Math.PI/180;

    const dx=x2-x1;
    const dy=y2-y1;

    const len=Math.sqrt(dx*dx+dy*dy);

    const nx=-dy/len;
    const ny=dx/len;

    const w=22.5;

    const p1=[x1+nx*w,y1+ny*w];
    const p2=[x1-nx*w,y1-ny*w];
    const p3=[x2-nx*w,y2-ny*w];
    const p4=[x2+nx*w,y2+ny*w];

    function ll(x,y){

        return [

            x/(R*Math.cos(lat0))*180/Math.PI,

            y/R*180/Math.PI

        ];

    }

    return{

        type:"Feature",

        properties:{

            name:runway.ref

        },

        geometry:{

            type:"Polygon",

            coordinates:[[

                ll(...p1),

                ll(...p2),

                ll(...p3),

                ll(...p4),

                ll(...p1)

            ]]

        }

    };

}

   draw(runways){

    const polygons=[];

    runways.forEach(r=>{

        polygons.push(
            this.runwayPolygon(r)
        );

    });

    if(this.map.getLayer("runway-outline")){

        this.map.removeLayer("runway-outline");

    }

    if(this.map.getLayer("runway-surface")){

        this.map.removeLayer("runway-surface");

    }

    if(this.map.getSource("runway-surface")){

        this.map.removeSource("runway-surface");

    }

    this.map.addSource("runway-surface",{

        type:"geojson",

        data:{

            type:"FeatureCollection",

            features:polygons

        }

    });

    this.map.addLayer({

        id:"runway-surface",

        type:"fill",

        source:"runway-surface",

        paint:{

            "fill-color":"#2e2e2e",

            "fill-outline-color":"#8a8a8a",

            "fill-opacity":1

        }

    });

    this.map.addLayer({

        id:"runway-outline",

        type:"line",

        source:"runway-surface",

        paint:{

            "line-color":"#bdbdbd",

            "line-width":1.5

        }

    });

}}